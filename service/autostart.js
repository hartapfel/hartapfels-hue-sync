'use strict';
var activity = 'luna://com.palm.activitymanager/';
var power = 'luna://com.webos.service.tvpower/power/getPowerState';
var name = 'C9 Screen Sync on power-on';

// An opt-in persistent Activity launches this service on a cold boot. While
// adopted, the TV power subscription also sees Quick Start standby and wake.
// The activity never requests a power lock.
module.exports = function autoStart(service, app, session, start) {
    var owner, sub, active, skipInitial = false, changing = false;
    var retry, reconnect, pendingStart, generation = 0, lastError = null, suppressed = false;
    function cancelRetry() { ++generation; clearTimeout(retry); retry = null; }
    function call(uri, params) {
        return new Promise(function (resolve, reject) {
            service.call(uri, params, function (msg) {
                if (msg.payload.returnValue === true) return resolve(msg.payload);
                var e = Error(msg.payload.errorText || 'TV service declined the startup setting.');
                e.code = msg.payload.errorCode;
                reject(e);
            });
        });
    }
    function onActive(token, attempt) {
        if (!owner || !active || suppressed || generation !== token) return;
        function followStream() {
            return session.whenEnded().then(function (end) {
                // Source changes can interrupt PicCap or HDMI after the first
                // successful send. The session settles after releasing the area.
                if (end.errorCode === 'VIDEO_NOT_READY') {
                    var err = Error(end.error);
                    err.code = end.errorCode;
                    throw err;
                }
            });
        }
        app.status().then(function (s) {
            if (!owner || !active || generation !== token || !s.autoStart || !s.area) return;
            if (s.state === 'stopping') return session.whenEnded().then(function () { onActive(token, attempt); });
            // A replacement power subscription can arrive before the original
            // start has settled. The new watcher inherits that attempt.
            if (pendingStart) return pendingStart.then(followStream);
            if (s.state === 'idle') {
                pendingStart = start().then(function () { pendingStart = null; lastError = null; },
                    function (err) { pendingStart = null; throw err; });
                return pendingStart.then(followStream);
            }
            if (s.state === 'starting' || s.state === 'streaming') return followStream();
        }).catch(function (err) {
            if (!owner || !active || generation !== token) return;
            lastError = err.message;
            var transient = /^(VIDEO_NOT_READY|ETIMEDOUT|ECONNREFUSED|ECONNRESET|EHOSTUNREACH|ENETUNREACH|ENETDOWN|HTTP_50[234])$/.test(err.code);
            if (err.code === 'AREA_BUSY' || (!transient && attempt >= 4)) { suppressed = true; return; }
            var delays = [2000, 5000, 10000, 20000, 30000];
            retry = setTimeout(function () { retry = null; onActive(token, attempt + 1); },
                delays[Math.min(attempt, delays.length - 1)]);
        });
    }
    function unwatch() {
        cancelRetry(); clearTimeout(reconnect);
        var old = sub;
        sub = null;
        if (old) old.cancel();
    }
    function watch() {
        var mine = service.subscribe(power, {subscribe: true});
        var first = true;
        sub = mine;
        function lost() {
            if (sub !== mine || !owner) return;
            unwatch();
            lastError = 'TV power updates are unavailable.';
            reconnect = setTimeout(watch, 15000);
        }
        mine.on('cancel', lost);
        mine.on('response', function (msg) {
            if (sub !== mine) return;
            var p = msg.payload;
            if (!p || !p.returnValue) return lost();
            var awake = p.state === 'Active' && p.onOff !== 'off';
            if (awake && p.processing) return; // Wait for a settled Active notification.
            if (!awake && p.onOff !== 'off' &&
                    ['Active Standby', 'Suspend', 'Power Off', 'Screen Off'].indexOf(p.state) < 0) return;
            var resume = first && active === true && awake;
            first = false;
            if (awake === active && !resume) return;
            active = awake;
            cancelRetry(); lastError = null;
            if (awake) {
                if (skipInitial) suppressed = true;
                onActive(generation, 0);
            } else {
                suppressed = false;
                session.stop();
            }
            skipInitial = false;
        });
    }
    function release() {
        unwatch();
        var previous = owner;
        owner = null; active = undefined;
        if (previous) previous.respond({returnValue: true});
    }
    service.register('onPowerOn', function (message) {
        var allowed = ['com.palm.activitymanager', 'com.webos.service.activitymanager'];
        if (allowed.indexOf(message.sender) < 0 || !message.payload.$activity) {
            return message.respond({returnValue: false, errorText: 'Startup requires a TV activity.'});
        }
        app.acceptsBoot(message.payload.registration).then(function (accepted) {
            if (!accepted) return message.respond({returnValue: true});
            release();
            // The webOS service framework adopts this message's activity before
            // delivery and completes it on respond. Hold it across standby.
            owner = message;
            watch();
        }).catch(function (err) {
            message.respond({returnValue: false, errorText: err.message});
        });
    });
    function enable() {
        var registration = require('crypto').randomBytes(16).toString('hex');
        return call(power, {}).then(function (p) {
            if (!p.state) throw Error('TV power state is unavailable.');
            return app.setAutoStart(true, registration);
        }).then(function () {
            skipInitial = true;
            return call(activity + 'create', { activity: {name: name, description: 'Start screen sync on TV power-on',
                type: {immediate: true, priority: 'low', persist: true, explicit: true, power: false},
                callback: {method: 'luna://org.webosbrew.c9screensync.service/onPowerOn', params: {registration: registration}}},
                start: true, replace: true, subscribe: false }).catch(function (err) {
                skipInitial = false;
                return app.setAutoStart(false).then(function () { throw err; });
            });
        });
    }
    function disable() {
        return app.setAutoStart(false).then(function () {
            release(); skipInitial = false;
            return call(activity + 'cancel', {activityName: name}).catch(function (err) {
                if (err.code !== 2) throw err;
            });
        });
    }
    return {
        set: function (enabled) {
            if (changing || typeof enabled !== 'boolean') return Promise.reject(Error('Invalid or pending startup change.'));
            changing = true;
            return (enabled ? enable() : disable()).then(function () {
                changing = false; lastError = null; return app.status();
            }, function (err) { changing = false; throw err; });
        },
        manualStop: function () { cancelRetry(); suppressed = true; lastError = null; },
        waiting: function () { return !!retry; },
        error: function () { return lastError; }
    };
};
