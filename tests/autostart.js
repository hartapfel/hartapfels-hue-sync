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
var session = { stop: function () { ++stops; live = false; return app.status(); } };
function init() { return create(service, app, session, function () { ++starts; live = true; return app.status(); }); }
function fire(id) {
    callback({sender: 'com.webos.service.activitymanager', payload: {$activity: {activityId: 10}, registration: id},
        respond: function () { ++responds; }});
    return later();
}
function power(value) { sub.emit('response', {payload: {returnValue: true, state: value}}); return later(); }
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
            boot.manualStop(); live = false;
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
        }).then(function () { console.log('Boot opt-in, wake, standby, cancellation and registration rollback passed on ' + process.version); })
        .catch(function (err) { console.error(err.stack); process.exitCode = 1; });
});
