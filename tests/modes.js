'use strict';
var assert = require('assert');
var encode = require('../service/worker-session');
var controller = require('../service/controller');
var register = require('../service/methods');
var bridge = {host:'127.0.0.1'}, keys = {applicationKey:'test',clientKey:Array(33).join('0')};
var area = {id:'00000000-0000-0000-0000-000000000001',channels:[{id:0,x:0,y:0,z:0}]};
function descriptor(source, mode) {
    return encode(bridge, keys, area, {width:320,height:180,fps:30,matrix:1,range:0,source:source,mode:mode}, 2100)[17];
}
assert.equal(descriptor('zones'),2);
assert.equal(descriptor('piccap','gaming'),5);
assert.equal(descriptor('zones','gaming'),6);
assert.equal(descriptor('zones','music'),10);
assert.throws(function () { descriptor('piccap','music'); });

var saved = {bridge:bridge,keys:keys,area:area,source:'piccap',brightness:53,intensity:2};
var state='idle', end, finished, handlers={}, starts=[];
var session={
    status:function () { return {state:state}; },
    start:function (client, bridge, keys, id, capture) {
        state='streaming'; starts.push(capture.mode);
        finished=new Promise(function (resolve) {end=resolve;});
        return Promise.resolve();
    },
    whenEnded:function () { return finished; },
    stop:function () { state='idle'; end({state:state}); return Promise.resolve(); }
};
var store={load:function () {return Promise.resolve(saved);},save:function (value) {saved=value;return Promise.resolve();}};
var app=controller({connect:function () {return {};}},store,session,function () {return Promise.resolve(true);});
register({register:function (name,cb) {handlers[name]=cb;},
    activityManager:{create:function (name,cb) {cb({activityId:1});},complete:function () {}}},app,session);
function call(name, mode) {
    return new Promise(function (resolve,reject) {
        handlers[name]({sender:'org.webosbrew.c9screensync',payload:{mode:mode},respond:function (r) {
            if (!r.returnValue) return reject(Error(r.errorText));
            resolve(r.result);
        }});
    });
}
call('start').then(function (s) {
    assert.equal(s.mode,'video'); return call('setMode','music');
}).then(function (s) {
    assert.equal(s.state,'streaming'); assert.equal(saved.mode,'music'); return call('setMode','gaming');
}).then(function (s) {
    assert.deepEqual(starts,['video','music','gaming']);
    assert.equal(s.brightness,53); assert.equal(s.intensity,2); assert.equal(saved.source,'piccap');
    console.log('Legacy Video default, live mode switches, persistence, and worker descriptors passed.');
}).catch(function (err) {console.error(err.stack);process.exitCode=1;});
