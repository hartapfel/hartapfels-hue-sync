'use strict';

var net = require('net');
var uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function integer(v, low, high) { return typeof v === 'number' && v === Math.floor(v) && v >= low && v <= high; }

// Private pipe between the LS2 service and the ARM worker. No credentials in argv or environment.
function encode(bridge, keys, area, capture, port) {
    if (!bridge || !keys || !area || !capture || !net.isIP(bridge.host) || !integer(port, 1, 65535) || !uuid.test(area.id) ||
        typeof keys.applicationKey !== 'string' || !/^[!-~]{1,128}$/.test(keys.applicationKey) ||
        typeof keys.clientKey !== 'string' || !/^[0-9a-f]{32}$/i.test(keys.clientKey) ||
        !Array.isArray(area.channels) || !integer(area.channels.length, 1, 256) ||
        !integer(capture.width, 1, 16384) || !integer(capture.height, 1, 16384) ||
        !integer(capture.fps, 1, 60) || !integer(capture.matrix, 0, 2) || !integer(capture.range, 0, 1))
        throw new Error('Invalid screen sync session.');
    var size = 17 + bridge.host.length + keys.applicationKey.length + 36 + 16 + area.channels.length * 25;
    var out = Buffer.alloc ? Buffer.alloc(size) : new Buffer(size);
    out.write('C9S1', 0, 4, 'ascii');
    out.writeUInt16LE(port, 4);
    out.writeUInt16LE(capture.width, 6);
    out.writeUInt16LE(capture.height, 8);
    out[10] = capture.fps;
    out[11] = capture.matrix;
    out[12] = capture.range;
    out.writeUInt16LE(area.channels.length, 13);
    out[15] = bridge.host.length;
    out[16] = keys.applicationKey.length;
    var offset = 17;
    [bridge.host, keys.applicationKey, area.id].forEach(function (value) {
        offset += out.write(value, offset, value.length, 'ascii');
    });
    offset += out.write(keys.clientKey, offset, 16, 'hex');
    var seen = {};
    area.channels.forEach(function (ch) {
        if (!integer(ch.id, 0, 255) || seen[ch.id]) throw new Error('Invalid entertainment channel.');
        seen[ch.id] = true;
        out[offset++] = ch.id;
        [ch.x, ch.y, ch.z].forEach(function (v) {
            if (typeof v !== 'number' || !(v >= -1 && v <= 1)) throw new Error('Invalid channel position.');
            out.writeDoubleLE(v, offset);
            offset += 8;
        });
    });
    return out;
}
module.exports = encode;
