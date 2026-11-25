'use strict';
var activity = 'luna://com.palm.activitymanager/';
var power = 'luna://com.webos.service.tvpower/power/getPowerState';
var name = 'C9 Screen Sync on power-on';

// An opt-in persistent Activity launches this service on a cold boot. While
// adopted, the TV power subscription also sees Quick Start standby and wake.
// The activity never requests a power lock.
module.exports = function autoStart(service, app, session, start) {
    var owner, sub, active, skipInitial = false, changing = false;
    var retry, reconnect, generation = 0, lastError = null;
    function cancelRetry() { ++generation; clearTimeout(retry); }
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
        if (!owner || !active || generation !== token) return;
        app.status().then(function (s) {
            if (!owner || !active || generation !== token || !s.autoStart || !s.area) return;
            if (s.state === 'stopping') return session.whenEnded().then(function () { onActive(token, attempt); });
            if (s.state === 'idle') return start().then(function () { if (generation === token) lastError = null; });
        }).catch(function (err) {
            if (!owner || !active || generation !== token) return;
            lastError = err.message;
            if (attempt < 4 && err.code !== 'AREA_BUSY') {
                retry = setTimeout(function () { onActive(token, attempt + 1); }, [2000, 5000, 10000, 20000][attempt]);
            }
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
            var awake = p.state === 'Active';
            if (!awake && ['Active Standby', 'Suspend', 'Power Off', 'Screen Off'].indexOf(p.state) < 0) return;
            if (awake === active) return;
            active = awake;
            cancelRetry(); lastError = null;
            if (awake && !skipInitial) onActive(generation, 0);
            if (!awake) session.stop();
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
        manualStop: cancelRetry,
        error: function () { return lastError; }
    };
};
