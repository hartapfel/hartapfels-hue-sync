'use strict';

module.exports = function register(service, app, session) {
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
                message.respond({ returnValue: false, errorText: 'Open C9 Screen Sync to use this control.' });
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
    add('status', function () { return app.status().then(function (s) {
        if (!s.error) s.error = boot.error();
        return s;
    }); });
    add('pair', function (params) { return app.pair(params.host); });
    add('areas', app.areas);
    add('select', function (params) { return app.select(params.id); });
    add('setSource', function (params) { return app.setSource(params.source); });
    add('start', start);
    add('stop', function () { boot.manualStop(); return app.stop(); });
    add('setAutoStart', function (params) { return boot.set(params.enabled); });
};
