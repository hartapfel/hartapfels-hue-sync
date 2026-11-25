'use strict';

// This controller is shared by user-initiated requests and the boot callback.
// Settings writes serialize with starts; stop always reaches a pending start.
module.exports = function controller(hue, store, session, checkAccess) {
    var settings, directAccess, writing = false, pendingWrite;
    var loaded = store.load().then(function (value) {
        settings = value;
        if (settings.brightness === undefined) settings.brightness = 100;
        if (settings.intensity === undefined) settings.intensity = 1;
        if (!settings.source) settings.source = 'direct';
    });
    function refreshAccess() {
        if (!checkAccess || settings.source !== 'direct') return Promise.resolve();
        return checkAccess().then(function (value) { directAccess = value; });
    }
    function status() {
        var state = session.status();
        return { state: state.state, error: state.error, bridge: settings.bridge || null,
            paired: Boolean(settings.keys), area: settings.area || null, autoStart: settings.autoStart === true,
            coldBootOnly: !!settings.lastColdBoot,
            source: settings.source, directAccess: directAccess, brightness: settings.brightness, intensity: settings.intensity };
    }
    function commit(changes) {
        var next = {};
        Object.keys(settings).forEach(function (key) { next[key] = settings[key]; });
        Object.keys(changes).forEach(function (key) { next[key] = changes[key]; });
        return store.save(next).then(function () { settings = next; return status(); });
    }
    function edit(fn, allowStreaming, waitForWrite) {
        return loaded.then(function () {
            if (writing && waitForWrite) {
                var resume = function () { return edit(fn, allowStreaming, true); };
                return pendingWrite.then(resume, resume);
            }
            if (writing || (!allowStreaming && session.status().state !== 'idle')) throw Error('Setup is busy.');
            writing = true;
            pendingWrite = Promise.resolve().then(fn).then(function (result) {
                writing = false;
                return result;
            }, function (err) { writing = false; throw err; });
            return pendingWrite;
        });
    }
    function client() {
        if (!settings.keys || !settings.bridge) throw Error('Pair the Hue Bridge first.');
        return hue.connect(settings.bridge, settings.keys.applicationKey);
    }
    return {
        status: function () { return loaded.then(refreshAccess).then(status); },
        pair: function (host) {
            return edit(function () {
                return hue.identify(host).then(function (bridge) {
                    return hue.pair(bridge).then(function (keys) {
                        var same = settings.bridge && settings.bridge.id === bridge.id;
                        return commit({ bridge: bridge, keys: keys, area: same ? settings.area : null });
                    });
                });
            });
        },
        areas: function () { return loaded.then(function () { return client().list(); }); },
        select: function (id) {
            return edit(function () {
                return client().layout(id).then(function (area) {
                    return commit({ area: { id: area.id, name: area.name } });
                });
            });
        },
        bootPreference: function () {
            return loaded.then(function () {
                return {enabled:settings.autoStart === true, cold:!!settings.lastColdBoot,
                    registration:settings.bootRegistration, scheduler:settings.bootScheduler};
            });
        },
        rememberScheduler: function (registration) {
            return edit(function () {
                if (!settings.autoStart || registration !== settings.bootRegistration) throw Error('Startup setting changed.');
                if (settings.bootScheduler === 'core') return status();
                return commit({bootScheduler:'core'});
            }, true, true);
        },
        acceptsBoot: function (registration, coreBoot) {
            return loaded.then(function () {
                return settings.autoStart === true && typeof registration === 'string' &&
                    registration === settings.bootRegistration && (settings.bootScheduler !== 'core' || coreBoot === true);
            });
        },
        needsColdBoot: function (registration, id) {
            return loaded.then(function () {
                return settings.autoStart && registration === settings.bootRegistration &&
                    settings.lastColdBoot !== id && settings.skipColdBoot !== id;
            });
        },
        skipColdBoot: function (id) {
            return edit(function () {
                if (settings.autoStart && settings.lastColdBoot && settings.lastColdBoot !== id && settings.skipColdBoot !== id)
                    return commit({skipColdBoot:id});
            }, true, true);
        },
        claimColdBoot: function (registration, id) {
            return edit(function () {
                if (!settings.autoStart || registration !== settings.bootRegistration ||
                        !settings.lastColdBoot || settings.lastColdBoot === id) return false;
                return commit({lastColdBoot:id}).then(function () { return true; });
            }, true, true);
        },
        setAutoStart: function (enabled, registration, bootId) {
            return edit(function () {
                if (typeof enabled !== 'boolean') throw Error('Choose a startup setting.');
                if (enabled && !settings.area) throw Error('Select an entertainment area first.');
                return commit({autoStart: enabled, bootScheduler:undefined, skipColdBoot:undefined, bootRegistration: enabled ? registration : undefined,
                    lastColdBoot: enabled ? bootId : undefined});
            }, true);
        },
        setSource: function (source) {
            return edit(function () {
                if (source !== 'direct' && source !== 'piccap') throw Error('Unknown video input.');
                return commit({ source: source }).then(function () { session.clearError(); return status(); });
            });
        },
        setPlayback: function (brightness, intensity) {
            return edit(function () {
                if (brightness !== Math.floor(brightness) || !(brightness >= 0 && brightness <= 100) ||
                    intensity !== Math.floor(intensity) || !(intensity >= 0 && intensity <= 3))
                    throw Error('Invalid brightness or video intensity.');
                return commit({brightness: brightness, intensity: intensity}).then(function (result) {
                    session.setPlayback(brightness, intensity);
                    return result;
                });
            }, true);
        },
        start: function () {
            return loaded.then(refreshAccess).then(function () {
                if (writing) throw Error('Bridge setup is in progress.');
                var connection = client();
                if (!settings.area) throw Error('Select an entertainment area first.');
                if (settings.source === 'direct' && directAccess === false) {
                    var error = Error('Grant Homebrew Channel permission to the Screen Sync capture service, then start sync again.');
                    error.code = 'CAPTURE_ACCESS_DENIED';
                    throw error;
                }
                return session.start(connection, settings.bridge, settings.keys, settings.area.id,
                    { source: settings.source, width: 320, height: 180, fps: 30, matrix: 1, range: 0,
                      brightness: settings.brightness, intensity: settings.intensity });
            });
        },
        stop: function () { return loaded.then(function () { return session.stop(); }); }
    };
};
