'use strict';

module.exports = function register(service, app, session) {
    var changingMode = false, stopGeneration = 0;
    function start() {
        return app.start().then(function () {
            var ended = session.whenEnded();
            // Hold an independent activity so closing the setup UI while
            // watching a movie does not release capture or stop the JS service.
            return new Promise(function (resolve, reject) {
                service.activityManager.create('C9 video sync', function (activity) {
                    if (!activity || !activity.activityId || activity.returnValue === false) {
                        var stopped = session.whenEnded() === ended ? session.stop() : Promise.resolve();
                        return stopped.then(function () { reject(Error('Could not keep TV sync active.')); }, reject);
                    }
                    ended.then(function () { service.activityManager.complete(activity); });
                    resolve(app.status());
                });
            });
        });
    }
    function add(name, fn) {
        service.register(name, function (message) {
            var sender = String(message.sender || '').split(' ')[0];
            if (sender !== 'org.webosbrew.c9screensync') {
                message.respond({ returnValue: false, errorText: "Open Hartapfel's Hue Sync to use this control." });
                return;
            }
            Promise.resolve().then(function () { return fn(message.payload || {}); }).then(function (result) {
                message.respond({ returnValue: true, result: result });
            }, function (err) {
                message.respond({ returnValue: false, errorText: err.message || 'Screen sync request failed.' });
            });
        });
    }
    var boot = require('./autostart')(service, app, session, start);
    add('status', function () { var setupError;
        return boot.ensure().catch(function (err) { setupError = err.message; }).then(app.status).then(function (s) {
        s.bootArmed = boot.armed();
        if (!s.error) s.error = setupError || boot.error();
        if (boot.waiting()) s.wakeRetry = true;
        return s;
    }); });
    add('pair', function (params) { return app.pair(params.host); });
    add('areas', app.areas);
    add('select', function (params) { return app.select(params.id); });
    add('setSource', function (params) { return app.setSource(params.source); });
    add('setPlayback', function (p) { return app.setPlayback(p.brightness, p.intensity); });
    add('setMode', function (p) {
        if (['video','music','gaming'].indexOf(p.mode) < 0 || changingMode) throw Error('Invalid or pending mode change.');
        changingMode = true;
        var token = stopGeneration;
        return app.status().then(function (s) {
            if (s.mode === p.mode) return s;
            if (s.state === 'idle') return app.setMode(p.mode);
            if (s.state !== 'streaming') throw Error('Sync is starting or stopping.');
            return app.stop().then(function () { return app.setMode(p.mode); }).then(function (updated) {
                return token === stopGeneration ? start() : updated;
            });
        }).then(function (s) { changingMode = false; return s; },
            function (err) { changingMode = false; throw err; });
    });
    add('start', function () { if (changingMode) throw Error('Switching sync mode.'); return start(); });
    add('stop', function () { ++stopGeneration; var stopped = app.stop(); return Promise.all([stopped,boot.manualStop()]).then(function (r) { return r[0]; }); });
    add('setAutoStart', function (params) { return boot.set(params.enabled); });
};
