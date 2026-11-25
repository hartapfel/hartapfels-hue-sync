'use strict';

// This controller is shared by user-initiated requests and the boot callback.
// Settings writes serialize with starts; stop always reaches a pending start.
module.exports = function controller(hue, store, session) {
    var settings, writing = false;
    var loaded = store.load().then(function (value) { settings = value; });
    function status() {
        var state = session.status();
        return { state: state.state, error: state.error, bridge: settings.bridge || null,
            paired: Boolean(settings.keys), area: settings.area || null, autoStart: settings.autoStart === true };
    }
    function commit(next) {
        return store.save(next).then(function () { settings = next; return status(); });
    }
    function edit(fn, allowStreaming) {
        return loaded.then(function () {
            if (writing || (!allowStreaming && session.status().state !== 'idle')) throw Error('Setup is busy.');
            writing = true;
            return Promise.resolve().then(fn).then(function (result) {
                writing = false;
                return result;
            }, function (err) { writing = false; throw err; });
        });
    }
    function client() {
        if (!settings.keys || !settings.bridge) throw Error('Pair the Hue Bridge first.');
        return hue.connect(settings.bridge, settings.keys.applicationKey);
    }
    return {
        status: function () { return loaded.then(status); },
        pair: function (host) {
            return edit(function () {
                return hue.identify(host).then(function (bridge) {
                    return hue.pair(bridge).then(function (keys) {
                        var same = settings.bridge && settings.bridge.id === bridge.id;
                        return commit({ version: 1, bridge: bridge, keys: keys, area: same ? settings.area : null,
                            autoStart: settings.autoStart === true, bootRegistration: settings.bootRegistration });
                    });
                });
            });
        },
        areas: function () { return loaded.then(function () { return client().list(); }); },
        select: function (id) {
            return edit(function () {
                return client().layout(id).then(function (area) {
                    return commit({ version: 1, bridge: settings.bridge, keys: settings.keys,
                        area: { id: area.id, name: area.name }, autoStart: settings.autoStart === true,
                        bootRegistration: settings.bootRegistration });
                });
            });
        },
        acceptsBoot: function (registration) {
            return loaded.then(function () {
                return settings.autoStart === true && typeof registration === 'string' &&
                    registration === settings.bootRegistration;
            });
        },
        setAutoStart: function (enabled, registration) {
            return edit(function () {
                if (typeof enabled !== 'boolean') throw Error('Choose a startup setting.');
                if (enabled && !settings.area) throw Error('Select an entertainment area first.');
                return commit({version: 1, bridge: settings.bridge, keys: settings.keys,
                    area: settings.area, autoStart: enabled, bootRegistration: enabled ? registration : undefined});
            }, true);
        },
        start: function () {
            return loaded.then(function () {
                if (writing) throw Error('Bridge setup is in progress.');
                var connection = client();
                if (!settings.area) throw Error('Select an entertainment area first.');
                return session.start(connection, settings.bridge, settings.keys, settings.area.id,
                    { width: 256, height: 144, fps: 30, matrix: 1, range: 0 });
            });
        },
        stop: function () { return loaded.then(function () { return session.stop(); }); }
    };
};
