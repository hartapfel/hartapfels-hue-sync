'use strict';
var assert = require('assert');
var Events = require('events').EventEmitter;
var create = require('../service/autostart');
var config = {}, registration, sub, callback, activity, responds = 0;
var starts = 0, stops = 0, canCreate = true, live = false;
function later(fn) { return new Promise(function (resolve) { setImmediate(function () { if (fn) fn(); resolve(); }); }); }
var service = {
    register: function (name, fn) { assert.equal(name, 'onPowerOn'); callback = fn; },
    call: function (uri, params, reply) {
        if (/getPowerState$/.test(uri)) return reply({payload: {returnValue: true, state: 'Active'}});
        if (/create$/.test(uri)) {
            if (!canCreate) return reply({payload: {returnValue: false, errorText: 'Activity unavailable'}});
            activity = params;
            return reply({payload: {returnValue: true, activityId: 10}});
        }
        assert(/cancel$/.test(uri));
        activity = null;
        reply({payload: {returnValue: true}});
    },
    subscribe: function () {
        sub = new Events();
        sub.cancel = function () { this.emit('cancel'); };
        return sub;
    }
};
var app = {
    status: function () { return Promise.resolve({autoStart: config.enabled, area: 'TV', state: live ? 'streaming' : 'idle'}); },
    acceptsBoot: function (id) { return Promise.resolve(config.enabled && registration === id); },
    setAutoStart: function (enabled, id) { config.enabled = enabled; registration = id; return app.status(); }
};
var ended, end;
function streaming() {
    live = true;
    ended = new Promise(function (resolve) { end = resolve; });
    return app.status();
}
function finish(reason) {
    live = false;
    if (end) end(reason || {state: 'idle', error: null});
    end = null;
    return app.status();
}
var session = {
    whenEnded: function () { return ended || app.status(); },
    stop: function () { ++stops; return finish(); }
};
function init(start) { return create(service, app, session, start || function () { ++starts; return streaming(); }); }
function fire(id) {
    callback({sender: 'com.webos.service.activitymanager', payload: {$activity: {activityId: 10}, registration: id},
        respond: function () { ++responds; }});
    return later();
}
function power(value, onOff, processing) {
    sub.emit('response', {payload: {returnValue: true, state: value, onOff: onOff, processing: processing}});
    return later();
}

function lateWake() {
    var set = global.setTimeout, clear = global.clearTimeout, timer, attempts = 0, failure = 'ETIMEDOUT';
    global.setTimeout = function (fn, ms) { assert(!timer); timer = {fn:fn, ms:ms}; return timer; };
    global.clearTimeout = function (item) { if (item === timer) timer = null; };
    function advance(ms) {
        assert(timer); assert.equal(timer.ms, ms);
        var fn = timer.fn; timer = null; fn(); return later();
    }
    function restore() { global.setTimeout = set; global.clearTimeout = clear; }
    var boot = init(function () {
        ++attempts;
        if (!failure) return streaming();
        var err = Error(failure); err.code = failure; return Promise.reject(err);
    });
    config.enabled = true; registration = 'saved'; live = false;
    var checks = fire(registration).then(function () { return power('Active', 'on', 'Prepare Power On'); })
        .then(function () { assert.equal(attempts, 0); return power('Active', 'on'); })
        .then(function () { return advance(2000); })
        .then(function () { return advance(5000); })
        .then(function () { return advance(10000); })
        .then(function () { failure = 'VIDEO_NOT_READY'; return advance(20000); })
        .then(function () { return advance(30000); })
        .then(function () {
            assert(boot.waiting()); assert.equal(attempts, 6);
            sub.cancel(); return advance(15000);
        }).then(function () { failure = null; return power('Active', 'on'); })
        .then(function () {
            assert(live); assert.equal(attempts, 7);
            // Reconnect the power subscription while the stream is healthy.
            // A single later video loss must schedule exactly one fresh start.
            sub.cancel(); return advance(15000);
        }).then(function () { return power('Active', 'on'); })
        .then(function () { return finish({state:'idle', errorCode:'VIDEO_NOT_READY', error:'PicCap disconnected'}); })
        .then(function () { return later(); })
        .then(function () {
            assert(boot.waiting()); assert.equal(boot.error(), 'PicCap disconnected');
            return advance(2000);
        }).then(function () {
            assert(live); assert.equal(attempts, 8);
            return power('Active', 'off', 'Prepare Suspend');
        }).then(function () {
            assert(!live); failure = 'ETIMEDOUT'; return power('Active', 'on');
        }).then(function () {
            assert(boot.waiting()); boot.manualStop(); assert(!timer); assert(!boot.waiting());
            sub.cancel(); return advance(15000);
        }).then(function () { return power('Active', 'on'); })
        .then(function () { assert.equal(attempts, 9); return power('Suspend'); })
        .then(function () { failure = 'AREA_BUSY'; return power('Active'); })
        .then(function () {
            assert(!timer); assert.equal(attempts, 10); assert.equal(boot.error(), 'AREA_BUSY');
            return boot.set(false);
        });
    return checks.then(restore, function (err) { restore(); throw err; });
}
setImmediate(function () {
    var boot = init();
    boot.set(true).then(function () {
        assert.equal(activity.activity.type.persist, true);
        assert.equal(activity.activity.type.power, false);
        assert.equal(activity.activity.callback.params.registration, registration);
        return fire('stale');
    }).then(function () {
        assert.equal(responds, 1);
        assert(!sub);
        return fire(registration);
    }).then(function () { return power('Active'); }).then(function () {
        assert.equal(starts, 0); // enabling the preference only arms the next wake
        return power('Suspend');
    }).then(function () { return power('Active'); }).then(function () { return power('Active'); })
        .then(function () {
            assert.equal(starts, 1); assert.equal(stops, 1);
            boot.manualStop(); return finish();
        }).then(function () {
            return power('Active');
        }).then(function () {
            assert.equal(starts, 1);
            boot = init(); // cold boot: framework starts a fresh service and re-delivers saved callback
            return fire(registration);
        }).then(function () { return power('Active'); }).then(function () {
            assert.equal(starts, 2);
            return boot.set(false);
        }).then(function () {
            assert.equal(config.enabled, false); assert.equal(activity, null);
            assert.equal(live, true); // disabling auto-start leaves the current session under manual control
            return power('Suspend');
        }).then(function () {
            assert.equal(stops, 1);
            canCreate = false;
            return boot.set(true).then(function () { throw Error('Expected creation failure'); }, function (err) {
                assert(/unavailable/.test(err.message)); assert.equal(config.enabled, false);
            });
        }).then(lateWake).then(function () { console.log('Boot opt-in, delayed HDMI and bridge, power reconnect, standby and cancellation passed on ' + process.version); })
        .catch(function (err) { console.error(err.stack); process.exitCode = 1; });
});
