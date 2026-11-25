'use strict';
var assert = require('assert');
var EventEmitter = require('events').EventEmitter;
var PassThrough = require('stream').PassThrough;
var createSession = require('../service/sync-session');
var area = { id: '00112233-4455-6677-8899-aabbccddeeff', channels: [{ id: 3, x: 0, y: 1, z: 0 }] };
var bridge = { host: '127.0.0.1' };
var keys = { applicationKey: 'test', clientKey: '00112233445566778899aabbccddeeff' };
var capture = { width: 128, height: 72, fps: 10, matrix: 1, range: 0 };

function rejected(p, pattern) {
    return p.then(function () { throw Error('Expected a rejection'); }, function (err) {
        assert(pattern.test(err.message), err.message);
    });
}

function fixture(startArea, behavior, layout) {
    var f = { launched: 0, activated: 0, released: 0, grants: 0, controls: [] };
    f.session = createSession(function () {
        ++f.launched;
        var child = new EventEmitter();
        child.stdin = new PassThrough();
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.stdin.on('data', function (buf) {
            if (buf.length === 3) {
                assert.equal(buf[0], 1);
                f.controls.push([buf[1], buf[2]]);
                ++f.grants;
                child.stdout.write('stream'); child.stdout.write('ing\n');
            } else if (buf.length === 2) f.controls.push([buf[0], buf[1]]);
            else assert.equal(buf.toString('ascii', 0, 4), 'C9S2');
        });
        child.kill = function () { setImmediate(function () { child.emit('close', null); }); };
        setImmediate(function () { behavior(child); });
        return child;
    });
    f.start = function () {
        return f.session.start({ layout: layout || available, start: function (id) {
            ++f.activated; return startArea(id);
        }, stop: function (id) {
            assert.equal(id, area.id);
            ++f.released;
            return Promise.resolve();
        } }, bridge, keys, area.id, capture);
    };
    return f;
}
function available() { return Promise.resolve(area); }
function ready(child) { child.stdout.write('video-'); child.stdout.write('ready\n'); }

function tests() {
    var first = fixture(available, ready);
    var start = first.start();
    return rejected(first.start(), /already in progress/).then(function () { return start; })
        .then(function (status) {
            assert.equal(status.state, 'streaming');
            assert.equal(first.activated, 1);
            assert.equal(first.grants, 1);
            first.session.setPlayback(37, 2);
            assert.deepEqual(first.controls, [[100,1], [100,1], [37,2]]);
            return Promise.all([first.session.stop(), first.session.stop()]);
        }).then(function () {
            assert.equal(first.released, 1);
            assert.deepEqual(first.session.status(), { state: 'idle', error: null });
            var early = fixture(available, ready);
            var cancelled = rejected(early.start(), /cancelled/);
            return Promise.all([cancelled, early.session.stop()]).then(function () {
                assert.equal(early.launched, 0);
                assert.equal(early.activated, 0);
                assert.equal(early.released, 0);
            });
        }).then(function () {
            var failed = fixture(available, function (child) {
                child.stderr.write('Video capture failed (-1)\n');
                child.emit('close', 1);
            });
            return rejected(failed.start(), /TV capture failed/).then(function () {
                assert.equal(failed.activated, 0);
                assert.equal(failed.released, 0);
                assert.equal(failed.session.status().state, 'idle');
            });
        }).then(function () {
            var runningChild;
            var interrupted = fixture(available, function (child) { runningChild = child; ready(child); });
            return interrupted.start().then(function () {
                var ended = interrupted.session.whenEnded();
                runningChild.stderr.write('PicCap input disconnected\n');
                runningChild.emit('close', 1);
                return ended;
            }).then(function (end) {
                assert.equal(end.state, 'idle');
                assert.equal(end.errorCode, 'VIDEO_NOT_READY');
                assert.equal(interrupted.released, 1);
            });
        }).then(function () {
            var failed = fixture(available, ready, function () { return Promise.reject(Error('Bridge unavailable')); });
            return rejected(failed.start(), /Bridge unavailable/).then(function () {
                assert.equal(failed.launched, 0);
                assert.equal(failed.released, 0);
            });
        }).then(function () {
            var confirm, begun;
            var activating = new Promise(function (resolve) { begun = resolve; });
            var slow = fixture(function () {
                begun();
                return new Promise(function (resolve) { confirm = resolve; });
            }, ready);
            var cancelled = rejected(slow.start(), /cancelled/);
            return activating.then(function () {
                var stopped = slow.session.stop();
                return new Promise(function (resolve) { setImmediate(resolve); }).then(function () {
                    assert.equal(slow.released, 0);
                    confirm(area);
                    return Promise.all([stopped, cancelled]);
                });
            }).then(function () { assert.equal(slow.grants, 0); assert.equal(slow.released, 1); });
        }).then(function () {
            var changed = fixture(function () {
                return Promise.resolve({id:area.id, channels:[{id:3, x:1, y:1, z:0}]});
            }, ready);
            return rejected(changed.start(), /layout changed/).then(function () {
                assert.equal(changed.grants, 0);
                assert.equal(changed.released, 1);
            });
        });
}

setImmediate(function () {
    tests().then(function () { console.log('Session lifecycle and cancellation passed on ' + process.version); },
        function (err) { console.error(err.stack); process.exitCode = 1; });
});
