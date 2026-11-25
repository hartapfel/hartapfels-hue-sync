'use strict';
var assert = require('assert');
var controller = require('../service/controller');
var boot = require('../service/autostart');
var companion = require('../capture-service/boot');
var token = '11223344556677889900aabbccddeeff';
var saved = {version:1,autoStart:true,area:{id:'test'},source:'direct',bootRegistration:token,lastColdBoot:'older'};
var writes = 0, claims = 0, starts = 0, applied, releaseSave;
var store = {load:function () {return Promise.resolve(saved);}, save:function (next) {
    ++writes;
    if (next.lastColdBoot !== saved.lastColdBoot) ++claims;
    return new Promise(function (resolve) {
        function finish() {saved = next; resolve();}
        if (writes === 1) releaseSave = finish;
        else finish();
    });
}};
var session = {status:function () {return {state:'idle'};},whenEnded:function () {return new Promise(function () {});}};
var app = controller({}, store, session);
var handlers = {}, schedule, confirmStream;
var device = {register:function (name,cb) {schedule=cb;},call:function (method,spec,cb) {
    assert(/create$/.test(method));
    assert.strictEqual(spec.activity.trigger.where.prop[1],'core-boot-done');
    assert.strictEqual(spec.activity.type.persist,true);
    applied=spec.activity.callback;
    cb({payload:{returnValue:true,activityId:91}});
}};
companion(device);
var running = boot({register:function (name,fn) {handlers[name]=fn;},
    call:function (method,p,cb) {
        assert(/scheduleBoot$/.test(method));
        schedule({sender:'org.webosbrew.c9screensync.service',payload:p,respond:function(reply){cb({payload:reply});}});
    },activityManager:{create:function (name,cb) {cb({activityId:92});},complete:function () {}}
},app,session,function () {++starts;return new Promise(function (resolve) {confirmStream=resolve;});},function () {return Promise.resolve('current');});
function tick() {return new Promise(function (resolve) {setImmediate(resolve);});}
function deliver(sender, params) {
    return new Promise(function (resolve) {
        handlers.onPowerOn({sender:'creator',ls2Message:{senderServiceName:function () {return sender;}},
            payload:params || applied.params,respond:resolve});
    });
}
var registering=running.ensure(), delivered, replied=false;
var deadline=setTimeout(function () { throw Error('Core-ready startup did not finish'); }, 3000);
tick().then(function () {
    assert(releaseSave);
    return deliver('unrelated.service');
}).then(function (r) {
    assert.strictEqual(r.returnValue,false);
    return deliver('com.webos.service.activitymanager.client',{coreBoot:true,registration:token,scheduledBoot:'older'});
}).then(function () {
    assert.equal(starts,0);
    delivered=Promise.all([deliver('com.webos.service.activitymanager.client'),
                        deliver('com.webos.service.activitymanager.client')]).then(function (replies) {
        assert(replies[0].returnValue && replies[1].returnValue); replied=true;
    });
}).then(tick).then(function () {
    assert(running.armed()); assert.equal(starts,1); assert.equal(claims,0); assert.equal(replied,false);
    confirmStream(); releaseSave(); return Promise.all([registering,delivered]);
}).then(function () {
    assert.equal(saved.bootScheduler,'core'); assert.equal(claims,1);
    return deliver('com.webos.service.activitymanager.client');
}).then(function () {
    assert.equal(starts,1);
    clearTimeout(deadline);
    console.log('Immediate core-ready startup, confirmed stream, duplicate delivery, setup race and caller checks passed on '+process.version);
}).catch(function (err) {clearTimeout(deadline);console.error(err.stack);process.exitCode=1;});
