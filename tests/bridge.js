'use strict';

// Fixture for the CLIP API. The production transport uses the same request options.
var assert = require('assert');
var Emitter = require('events').EventEmitter;
var https = require('https');
var hue = require('../service/hue-bridge');
var host = '127.0.0.1';
var id = '001788fffe123456';
var areaId = '10000000-0000-4000-8000-000000000001';
var owner = '10000000-0000-4000-8000-000000000002';
var other = '10000000-0000-4000-8000-000000000003';
var area = { id: areaId, metadata: { name: 'TV' }, type: 'entertainment_configuration',
    status: 'inactive', active_streamer: null, channels: [
        { channel_id: 17, position: { x: -0.4, y: 0.8, z: 0.4 } },
        { channel_id: 5, position: { x: 0, y: -0.8, z: 0 } }
    ] };
var bridge, client, button = false, writes = 0;
var base = '/clip/v2/resource/entertainment_configuration';
var certId = id;

https.request = function (options, receive) {
    var req = new Emitter();
    req.end = function (payload) {
        setImmediate(function () {
            assert.strictEqual(options.rejectUnauthorized, true);
            assert.strictEqual(options.ca.length, 2);
            var failed = options.checkServerIdentity(host, {subject: { CN: certId }});
            if (failed) return req.emit('error', failed);
            var body, headers = {};
            if (options.path === '/api/config') {
                body = {bridgeid: id.toUpperCase(), name: 'Fixture', modelid: 'BSB003'};
            } else if (options.path === '/api') {
                assert.deepEqual(JSON.parse(payload), {devicetype:'c9_screen_sync#lg_tv', generateclientkey:true});
                body = button ? [{success:{username:'test-key', clientkey:'00000000000000000000000000000002'}}] :
                    [{error:{type:101}}];
            } else {
                assert.strictEqual(options.headers['hue-application-key'], 'test-key');
                if (options.path === '/auth/v1') headers['hue-application-id'] = owner;
                else if (options.path === base || options.path === base + '/' + areaId) {
                    if (options.method === 'PUT') {
                        writes++;
                        var start = JSON.parse(payload).action === 'start';
                        area.status = start ? 'active' : 'inactive';
                        area.active_streamer = start ? {rid:owner} : null;
                        body = {errors:[], data:[{rid:area.id, rtype:area.type}]};
                    } else body = {errors:[], data:[area]};
                } else throw new Error('Unexpected route');
            }
            var res = new Emitter();
            res.statusCode = 200;
            res.headers = headers;
            receive(res);
            if (body) {
                var json = JSON.stringify(body);
                res.emit('data', Buffer.from ? Buffer.from(json) : new Buffer(json));
            }
            res.emit('end');
        });
    };
    return req;
};

function rejected(work, expected) {
    return work.then(function () { throw new Error('Expected failure: ' + expected); }, function (err) {
        assert(expected.test(err.code || err.message), err.message);
    });
}

setImmediate(function () {
    hue.identify(host).then(function (b) {
        bridge = b;
        assert.strictEqual(b.id, id);
        return rejected(hue.pair(b), /LINK_BUTTON/);
    }).then(function () {
        button = true;
        return hue.pair(bridge);
    }).then(function (keys) {
        assert.strictEqual(keys.clientKey.length, 32);
        client = hue.connect(bridge, keys.applicationKey);
        return client.list();
    }).then(function (list) {
        assert.strictEqual(list[0].name, 'TV');
        return client.start(areaId);
    }).then(function (a) {
        assert.deepEqual(a.channels, [{id:17, x:-0.4, y:0.8, z:0.4}, {id:5, x:0, y:-0.8, z:0}]);
        assert.strictEqual(a.owner, owner);
        return client.start(areaId);
    }).then(function () {
        assert.strictEqual(writes, 1);
        return client.stop(areaId);
    }).then(function () {
        assert.strictEqual(area.status, 'inactive');
        area.status = 'active';
        area.active_streamer = {rid:other};
        return rejected(client.start(areaId), /AREA_BUSY/);
    }).then(function () {
        return client.stop(areaId);
    }).then(function () {
        assert.strictEqual(writes, 2);
        assert.strictEqual(area.active_streamer.rid, other);
        area.channels[1].channel_id = 17;
        return rejected(client.layout(areaId), /Invalid channel/);
    }).then(function () {
        certId = '001788fffe654321';
        return rejected(client.list(), /certificate/);
    }).then(function () {
        console.log('Bridge pairing, channel layout, stream ownership and bridge identity checks passed on ' + process.version);
    }).catch(function (err) {
        console.error(err.stack);
        process.exitCode = 1;
    });
});
