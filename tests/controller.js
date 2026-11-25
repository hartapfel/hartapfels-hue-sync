'use strict';
var assert = require('assert');
var controller = require('../service/controller');
var saved = { version: 1 }, state = 'idle', failSave = false, linkPressed = false;
var store = {
    load: function () { return Promise.resolve(saved); },
    save: function (next) {
        if (failSave) return Promise.reject(Error('disk full'));
        saved = next;
        return Promise.resolve();
    }
};
var room = { id: 'room', name: 'TV' };
var client = {
    list: function () { return Promise.resolve([room]); },
    layout: function (id) { assert.equal(id, room.id); return Promise.resolve(room); }
};
var hue = {
    identify: function (host) { return Promise.resolve({ host:host, id:host }); },
    pair: function () {
        return linkPressed ? Promise.resolve({ applicationKey:'app-test', clientKey:'stream-test' }) :
            Promise.reject(Error('Press link button'));
    },
    connect: function (bridge, key) { assert.equal(key, 'app-test'); return client; }
};
var updates = [], launches = [];
var session = {
    setPlayback: function (brightness, intensity) { updates.push([brightness, intensity]); },
    status: function () { return { state:state, error:null }; },
    start: function (connection, bridge, keys, id, capture) {
        assert.equal(connection, client);
        assert.equal(keys.clientKey, 'stream-test');
        assert.equal(id, room.id);
        assert.equal(capture.fps, 30);
        assert.equal(capture.source, 'direct');
        assert.equal(capture.width, 320);
        assert.equal(capture.height, 180);
        launches.push([capture.brightness, capture.intensity]);
        state = 'streaming';
        return Promise.resolve(this.status());
    },
    stop: function () { state = 'idle'; return Promise.resolve(this.status()); }
};
function rejects(p, pattern) {
    return p.then(function () { throw Error('Expected rejection'); },
        function (err) { assert(pattern.test(err.message)); });
}
var app = controller(hue, store, session, function () { return Promise.resolve(true); });
setImmediate(function () {
    rejects(app.start(), /Pair/).then(function () { return rejects(app.pair('a'), /link/); })
        .then(function () { linkPressed = true; return app.pair('a'); })
        .then(function (info) {
            assert.equal(info.paired, true);
            assert(!/app-test|stream-test/.test(JSON.stringify(info)));
            return rejects(app.start(), /Select/);
        }).then(function () { return app.areas(); })
        .then(function (areas) { assert.deepEqual(areas, [room]); return app.select(room.id); })
        .then(function (s) { assert.equal(s.source, 'direct'); return app.setSource('piccap'); })
        .then(function () { return controller(hue, store, session).status(); })
        .then(function (s) { assert.equal(s.source, 'piccap'); return app.setSource('direct'); })
        .then(function () { return rejects(app.setSource('invalid'), /Unknown/); })
        .then(function () {
            var denied = controller(hue, store, session, function () { return Promise.resolve(false); });
            return denied.status().then(function (s) {
                assert.strictEqual(s.directAccess, false);
                return rejects(denied.start(), /Select PicCap/);
            });
        }).then(function () { return app.start(); })
        .then(function () {
            assert.deepEqual(launches, [[100,1]]);
            return app.setPlayback(48, 3);
        }).then(function (s) {
            assert.equal(s.state, 'streaming');
            assert.deepEqual(updates, [[48,3]]);
            return rejects(app.setPlayback(101, 3), /Invalid/);
        }).then(function () { return rejects(app.setPlayback(25, 4), /Invalid/); })
        .then(function () { failSave = true; return rejects(app.setPlayback(5, 0), /disk full/); })
        .then(function () { failSave = false; assert.deepEqual(updates, [[48,3]]); return app.stop(); })
        .then(function () { return controller(hue, store, session, function () { return Promise.resolve(true); }).start(); })
        .then(function () { assert.deepEqual(launches, [[100,1],[48,3]]); })
        .then(function () { return rejects(app.pair('b'), /busy/); })
        .then(function () { return app.stop(); })
        .then(function () { failSave = true; return rejects(app.pair('b'), /disk full/); })
        .then(function () { failSave = false; return app.status(); })
        .then(function (info) { assert.equal(info.bridge.host, 'a'); return app.pair('a'); })
        .then(function (info) { assert.deepEqual(info.area, room); return app.pair('b'); })
        .then(function (info) {
            assert.equal(info.area, null);
            console.log('Bridge setup, selection, start, repair and write failure passed on ' + process.version);
        }).catch(function (err) { console.error(err.stack); process.exitCode = 1; });
});
