'use strict';
var activity = 'luna://com.palm.activitymanager/';
var power = 'luna://com.webos.service.tvpower/power/getPowerState';
var name = 'C9 Screen Sync on power-on';
function readBootId() {
    return new Promise(function (resolve, reject) {
        require('fs').readFile('/proc/sys/kernel/random/boot_id', 'utf8', function (err, raw) {
            if (err) return reject(err);
            var id = raw.trim();
            if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id))
                return reject(Error('Could not identify this TV boot.'));
            resolve(id);
        });
    });
}

// An opt-in persistent Activity launches this service on a cold boot. While
// adopted, the TV power subscription also sees Quick Start standby and wake.
// The activity never requests a power lock.
module.exports = function autoStart(service, app, session, start, readBoot) {
    readBoot = readBoot || readBootId;
    var owner, duplicateCallbacks = [], sub, active, skipInitial = false, changing = false;
    var coreSeen, coreRequest, coreHold, finishStartup;
    function releaseCoreHold() {
        if (coreHold) { service.activityManager.complete(coreHold); coreHold = null; }
        if (finishStartup) { finishStartup(); finishStartup = null; }
    }
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
    function started(token) {
        if (owner && generation === token && owner.bootId) {
            return app.claimColdBoot(owner.payload.registration, owner.bootId).then(function () {
                console.log('Screen Sync startup: Hue stream confirmed');
                releaseCoreHold();
            });
        }
        releaseCoreHold();
    }
    function onActive(token, attempt) {
        if (!owner || !active || suppressed || generation !== token) return;
        function followStream() {
            return session.whenEnded().then(function (end) {
                // Source changes can interrupt PicCap or HDMI after the first
                // successful send. The session settles after releasing the area.
                if (end.errorCode === 'VIDEO_NOT_READY' && !ownerColdBootOnly()) {
                    var err = Error(end.error);
                    err.code = end.errorCode;
                    throw err;
                }
            });
        }
        app.status().then(function (s) {
            if (!owner || !active || generation !== token) return;
            if (!s.autoStart || !s.area) return releaseCoreHold();
            if (s.state === 'stopping') return session.whenEnded().then(function () { onActive(token, attempt); });
            // A replacement power subscription can arrive before the original
            // start has settled. The new watcher inherits that attempt.
            if (pendingStart) return pendingStart.then(followStream);
            if (s.state === 'idle') {
                if (coreHold) console.log('Screen Sync startup: starting saved entertainment area');
                pendingStart = start().then(function () { pendingStart = null; lastError = null; return started(token); },
                    function (err) { pendingStart = null; throw err; });
                return pendingStart.then(followStream);
            }
            if (s.state === 'streaming') return Promise.resolve(started(token)).then(followStream);
            if (s.state === 'starting') return followStream();
        }).catch(function (err) {
            if (!owner || !active || generation !== token) return;
            lastError = err.message;
            if (coreHold) console.log('Screen Sync startup: attempt ' + (attempt + 1) + ' failed (' + (err.code || 'start') + ')');
            var transient = /^(VIDEO_NOT_READY|ETIMEDOUT|ECONNREFUSED|ECONNRESET|EHOSTUNREACH|ENETUNREACH|ENETDOWN|HTTP_50[234])$/.test(err.code);
            if (err.code === 'TV_STANDBY' || err.code === 'AREA_BUSY' || err.code === 'CAPTURE_ACCESS_DENIED' || (!transient && attempt >= 4)) {
                suppressed = true; releaseCoreHold();
                return;
            }
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
    function release(error) {
        unwatch();
        var previous = owner;
        owner = null; active = undefined;
        var reply = error ? {returnValue:false, errorText:error.message} : {returnValue:true};
        if (previous) previous.respond(reply);
        duplicateCallbacks.splice(0).forEach(function (message) { message.respond(reply); });
    }
    function ownerColdBootOnly() {
        return owner && owner.payload.coldBootOnly === true;
    }
    function handleColdBoot(message, token) {
        return readBoot().then(function (id) {
            return app.claimColdBoot(message.payload.registration, id);
        }).then(function (fresh) {
            if (owner !== message || generation !== token) return;
            lastError = null;
            if (!fresh) return;
            active = true;
            suppressed = false;
            onActive(token, 0);
        }, function (err) {
            if (owner !== message || generation !== token) return;
            lastError = err.message;
            // Keep the persistent activity adopted while storage is busy.
            retry = setTimeout(function () { retry = null; handleColdBoot(message, token); }, 5000);
        });
    }
    function coreBoot(message) {
        var registration = message.payload.registration;
        // Updates can leave a one-shot callback from 0.6.5/6 in the scheduler.
        // Only the persistent core-ready trigger should initiate a new start.
        if (message.payload.scheduledBoot) return message.respond({returnValue:true});
        app.acceptsBoot(registration, true).then(function (accepted) {
            if (!accepted) return message.respond({returnValue:true});
            coreSeen = registration;
            if (!coreRequest) {
                var token = generation;
                coreRequest = readBoot().then(function (id) {
                    return app.needsColdBoot(registration, id).then(function (fresh) {
                        if (!fresh || token !== generation) return;
                        if (owner && owner.bootId === id) return;
                        return new Promise(function (resolve, reject) {
                            service.activityManager.create('C9 startup sync', function (activity) {
                                if (!activity || !activity.activityId) return reject(Error('Could not keep startup active.'));
                                if (token !== generation) { service.activityManager.complete(activity); return resolve(); }
                                release();
                                coreHold = activity;
                                owner = {payload:{coldBootOnly:true,registration:registration},bootId:id,respond:releaseCoreHold};
                                active = true; suppressed = false;
                                // Keep the scheduler's adopted callback until the
                                // streaming activity takes ownership or startup ends.
                                finishStartup = resolve;
                                console.log('Screen Sync startup: checking capture service');
                                onActive(generation, 0);
                            });
                        });
                    });
                });
                coreRequest = coreRequest.then(function () { coreRequest = null; },
                    function (err) { coreRequest = null; throw err; });
            }
            return coreRequest.then(function () { message.respond({returnValue:true}); });
        }).catch(function (err) { lastError = err.message; message.respond({returnValue:false, errorText:err.message}); });
    }
    service.register('onPowerOn', function (message) {
        var allowed = ['com.palm.activitymanager', 'com.webos.service.activitymanager', 'com.webos.service.activitymanager.client'];
        var caller = message.ls2Message && message.ls2Message.senderServiceName ? message.ls2Message.senderServiceName() : message.sender;
        var system = allowed.indexOf(String(caller || '').split(' ')[0]) >= 0;
        // The C9 sends continuous triggers through the scheduler's client
        // bus name. Its callback can omit the ordinary $activity envelope.
        if (system && message.payload.coreBoot === true) return coreBoot(message);
        if (!system || !message.payload.$activity) {
            return message.respond({returnValue: false, errorText: 'Startup requires a TV activity.'});
        }
        app.acceptsBoot(message.payload.registration, message.payload.coreBoot).then(function (accepted) {
            if (!accepted) return message.respond({returnValue: true});
            // Responding completes the associated webOS activity. Hold duplicate
            // deliveries alongside the original until we release that activity.
            if (owner && owner.payload.registration === message.payload.registration &&
                    owner.payload.$activity && owner.payload.$activity.activityId === message.payload.$activity.activityId) {
                duplicateCallbacks.push(message);
                return;
            }
            release();
            // The webOS service framework adopts this message's activity before
            // delivery and completes it on respond. Hold it across standby.
            owner = message;
            if (ownerColdBootOnly()) return handleColdBoot(message, generation);
            watch();
        }).catch(function (err) {
            if (owner === message) {
                lastError = err.message;
                release(err);
            } else {
                message.respond({returnValue: false, errorText: err.message});
            }
        });
    });
    var companion = 'luna://org.webosbrew.c9screensync.capture/scheduleBoot';
    var installing, checkedRegistration;
    function armCore(registration) {
        if (installing) return installing;
        installing = call(companion, {enabled:true,registration:registration}).then(function () {
            checkedRegistration = registration;
            return app.rememberScheduler(registration);
        }).then(function (r) { installing = null; return r; },
            function (err) { installing = null; throw err; });
        return installing;
    }
    function ensure() {
        if (!app.bootPreference || changing) return Promise.resolve();
        return app.bootPreference().then(function (p) {
            if (p.enabled && p.cold && (p.scheduler !== 'core' || (!coreSeen && !checkedRegistration))) return armCore(p.registration);
        });
    }
    function enable() {
        var registration = require('crypto').randomBytes(16).toString('hex');
        var params = {registration:registration};
        return call(power, {}).then(function (p) {
            if (!p.state) throw Error('TV power state is unavailable.');
        }, function (err) {
            if (!/denied|permission|not permitted/i.test(err.message)) throw err;
            params.coldBootOnly = true;
            return readBoot();
        }).then(function (id) {
            return app.setAutoStart(true, registration, id);
        }).then(function () {
            if (params.coldBootOnly && app.bootPreference) {
                return armCore(registration).catch(function (err) {
                    return app.setAutoStart(false).then(function () { throw err; });
                });
            }
            skipInitial = !params.coldBootOnly;
            return call(activity + 'create', { activity: {name: name, description: 'Start screen sync on TV power-on',
                type: {immediate: true, priority: 'low', persist: true, explicit: true, power: false},
                callback: {method: 'luna://org.webosbrew.c9screensync.service/onPowerOn', params: params}},
                start: true, replace: true, subscribe: false }).catch(function (err) {
                skipInitial = false;
                return app.setAutoStart(false).then(function () { throw err; });
            });
        });
    }
    function disable() {
        var pref = app.bootPreference ? app.bootPreference() : Promise.resolve({});
        return pref.then(function (p) {
            if (p.cold && app.bootPreference) return call(companion, {enabled:false});
        }).then(function () { return app.setAutoStart(false); }).then(function () {
            release(); coreSeen = null; checkedRegistration = null; skipInitial = false;
            return call(activity + 'cancel', {activityName: name}).catch(function (err) {
                if (err.code !== 2) throw err;
            });
        });
    }
    return {
        ensure: ensure,
        armed: function () { return !!coreSeen; },
        set: function (enabled) {
            if (changing || typeof enabled !== 'boolean') return Promise.reject(Error('Invalid or pending startup change.'));
            changing = true;
            return (enabled ? enable() : disable()).then(function () {
                changing = false; lastError = null; return app.status();
            }, function (err) { changing = false; throw err; });
        },
        manualStop: function () { releaseCoreHold(); cancelRetry(); suppressed = true; lastError = null;
            if (app.skipColdBoot) return readBoot().then(app.skipColdBoot); },
        waiting: function () { return !!retry; },
        error: function () { return lastError; }
    };
};
