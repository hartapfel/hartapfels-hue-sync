'use strict';
var assert = require('assert');
var register = require('../service/methods');
var appId = 'org.webosbrew.c9screensync';
var methods = {}, held = 0, released = 0, ended, end, state = 'idle';
var app = {
    status: function () { return Promise.resolve({ state: state }); },
    start: function () {
        state = 'streaming';
        ended = new Promise(function (resolve) { end = resolve; });
        return app.status();
    },
    stop: function () {
        state = 'idle';
        end();
        return app.status();
    }
};
var allow = true;
var service = { register: function (name, fn) { methods[name] = fn; }, activityManager: {
    create: function (name, fn) {
        if (allow) fn({ activityId: ++held });
        else fn({ returnValue: false });
    },
    complete: function () { ++released; }
} };
register(service, app, { whenEnded: function () { return ended; }, stop: app.stop });
function call(name, sender) {
    return new Promise(function (resolve) {
        methods[name]({ sender: sender, payload: {}, respond: resolve });
    });
}
setImmediate(function () {
    call('start', 'unrelated.app').then(function (r) {
        assert.equal(r.returnValue, false);
        assert.equal(state, 'idle');
        return call('start', appId + ' 813');
    }).then(function (r) {
        assert.deepEqual(r, { returnValue: true, result: { state: 'streaming' } });
        assert.equal(released, 0);
        return call('stop', appId);
    }).then(function () {
        assert.equal(released, 1);
        allow = false;
        return call('start', appId);
    }).then(function (r) {
        assert.equal(r.returnValue, false);
        assert.equal(state, 'idle');
        console.log('Caller validation, activity lifetime, and failure cleanup passed on ' + process.version);
    }).catch(function (err) { console.error(err.stack); process.exitCode = 1; });
});
