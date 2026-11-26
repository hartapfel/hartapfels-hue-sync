'use strict';
var assert = require('assert');
var EventEmitter = require('events').EventEmitter;
var start, cancel, upstream, cancels = 0;
var status = require('../capture-service/power')({
    register:function (_, opened, closed) { start = opened; cancel = closed; },
    subscribe:function (uri, args) {
        assert.equal(uri, 'luna://com.webos.service.tvpower/power/getPowerState');
        assert(args.subscribe);
        upstream = new EventEmitter();
        upstream.cancel = function () { ++cancels; upstream.emit('cancel'); };
        return upstream;
    }
});
var responses = [];
var msg = {sender:'other.app', isSubscription:true, uniqueToken:'session',
    respond:function (p) { responses.push(p); }, cancel:function (p) { responses.push(p); }};
start(msg);
assert.strictEqual(responses.pop().returnValue, false);
assert.equal(status(), 'idle');
msg.sender = 'org.webosbrew.c9screensync.service';
start(msg);
function state(p) { p.returnValue = true; upstream.emit('response', {payload:p}); }
state({state:'Active'});
assert.strictEqual(responses.pop().standby, false);
state({state:'Active', processing:'Request Power Off'});
assert.strictEqual(responses.pop().standby, true);
state({state:'Active Standby'});
assert.equal(status(), 'standby');
cancel({uniqueToken:'unrelated'});
assert.equal(cancels, 0);
cancel(msg);
assert.equal(status(), 'idle');
start(msg);
upstream.emit('response', {payload:{returnValue:false}});
assert.equal(status(), 'unavailable');
assert.strictEqual(responses.pop().returnValue, false);
console.log('Power transitions, subscription ownership and cancellation passed.');
