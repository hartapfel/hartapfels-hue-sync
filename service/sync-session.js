'use strict';

var spawn = require('child_process').spawn;
var path = require('path');
var encode = require('./worker-session');

function deferred() {
    var pair = {};
    pair.promise = new Promise(function (resolve, reject) {
        pair.resolve = resolve;
        pair.reject = reject;
    });
    return pair;
}

// One capture owner and one bridge stream at a time, including while stopping.
module.exports = function createSession(launch) {
    var current = null, lastError = null;
    launch = launch || function () {
        return spawn(path.join(__dirname, 'c9-sync'), ['--live-controls'], { stdio: ['pipe', 'pipe', 'pipe'] });
    };

    function status() {
        var state = { state: current ? current.state : 'idle', error: lastError ? lastError.message : null };
        if (lastError && lastError.code) state.errorCode = lastError.code;
        return state;
    }

    function finish(s, error) {
        if (s.finishing) return;
        s.finishing = true;
        clearTimeout(s.deadline);
        clearTimeout(s.killTimer);
        s.state = 'stopping';
        function done(releaseError) {
            var reason = releaseError || error;
            lastError = reason;
            current = null;
            if (!s.ready) s.start.reject(reason || new Error('Start cancelled.'));
            s.end.resolve(status());
        }
        if (!s.activation || (error && error.code === 'AREA_BUSY')) return done(null);
        // The worker may exit while the activation request is in flight. Let
        // it settle before checking ownership, including after an HTTP timeout.
        function release() { return s.client.stop(s.area); }
        s.activation.then(release, release).then(function () { done(null); }, done);
    }

    function terminate(s, error) {
        if (s.finishing || s.state === 'stopping') return;
        s.state = 'stopping';
        s.failure = error;
        clearTimeout(s.deadline);
        if (s.child) {
            if (!s.child.stdin.finished) s.child.stdin.end();
            s.child.kill();
            s.killTimer = setTimeout(function () { s.child.kill('SIGKILL'); }, 2000);
        }
    }

    function start(client, bridge, keys, area, capture) {
        if (current) return Promise.reject(new Error('A screen sync session is already in progress.'));
        var s = { state: 'starting', area: area, client: client, start: deferred(), end: deferred() };
        s.controls = new Buffer([capture.brightness === undefined ? 100 : capture.brightness,
                                 capture.intensity === undefined ? 1 : capture.intensity]);
        current = s;
        lastError = null;
        Promise.resolve().then(function () { return client.layout(area); }).then(function (layout) {
            if (s.state === 'stopping') return finish(s, s.failure);
            var descriptor = encode(bridge, keys, layout, capture, 2100);
            s.child = launch();
            var pending = '', diagnostic = '', granted = false;
            function activate() {
                s.activation = client.start(area);
                s.activation.then(function (confirmed) {
                    if (s.state !== 'starting') return;
                    // Positions from the read-only layout are already in the
                    // worker. A fresh start is needed if the Hue app edited them.
                    var positions = {};
                    confirmed.channels.forEach(function (ch) { positions[ch.id] = ch; });
                    if (confirmed.id !== layout.id || confirmed.channels.length !== layout.channels.length ||
                            !layout.channels.every(function (ch) {
                                var p = positions[ch.id];
                                return p && p.x === ch.x && p.y === ch.y && p.z === ch.z;
                            })) throw Error('The entertainment layout changed while starting. Try again.');
                    granted = true;
                    s.child.stdin.write(Buffer.concat([new Buffer([1]), s.controls]));
                    s.canUpdate = true;
                }).catch(function (err) { terminate(s, err); });
            }
            s.child.stderr.setEncoding('utf8');
            s.child.stderr.on('data', function (chunk) {
                diagnostic = (diagnostic + chunk).slice(-1024);
            });
            s.child.on('error', function () {
                s.failure = new Error('Could not run the screen capture worker.');
                // Node emits close after a spawn error and after stdio closes.
            });
            s.child.on('close', function () {
                var error = s.failure;
                if (s.state !== 'stopping' && !error) {
                    var message = /handshake failed/.test(diagnostic) ? 'The Hue stream handshake failed.' :
                        /DILE capture device access denied|Creating handle to \/dev\/mem failed/.test(diagnostic) ?
                            'webOS denied this app access to the native capture device. Use PicCap stream on this TV.' :
                        /DILE capture start failed|DILE capture initialization failed/.test(diagnostic) ?
                            'LG capture could not start. Pause PicCap capture and try Direct TV capture again.' :
                        /PicCap input/i.test(diagnostic) ? 'PicCap input unavailable. Set PicCap to TCP 127.0.0.1 port 19401 and start capture.' :
                        /capture failed|No video frame|video backend/i.test(diagnostic) ?
                            'TV capture failed. Check the video input and whether PicCap is using capture.' :
                            'The screen capture worker exited.';
                    error = new Error(message);
                    if (/No PicCap input|PicCap input disconnected|No video frame/.test(diagnostic)) {
                        error.code = 'VIDEO_NOT_READY';
                    }
                }
                finish(s, error);
            });
            s.child.stdin.on('error', function () {
                terminate(s, new Error('Could not configure the screen capture worker.'));
            });
            s.child.stdout.setEncoding('utf8');
            s.child.stdout.on('data', function (chunk) {
                if (s.state !== 'starting') return;
                pending += chunk.replace(/\r/g, '');
                if (pending.length > 64) return terminate(s, new Error('Invalid worker response.'));
                if (pending === 'video-ready\n' && !s.activation) {
                    pending = '';
                    activate();
                } else if (pending === 'streaming\n' && granted) {
                    clearTimeout(s.deadline);
                    s.state = 'streaming';
                    s.ready = true;
                    s.start.resolve(status());
                } else if (pending.indexOf('\n') !== -1) terminate(s, new Error('Unexpected worker response.'));
            });
            s.deadline = setTimeout(function () {
                var error = new Error('The first lighting frame timed out.');
                if (!s.activation) error.code = 'VIDEO_NOT_READY';
                terminate(s, error);
            }, 45000);
            s.child.stdin.write(s.controls);
            s.child.stdin.write(descriptor, function () { descriptor.fill(0); });
        }).catch(function (err) {
            if (s.child) terminate(s, err);
            else finish(s, s.state === 'stopping' ? s.failure : err);
        });
        return s.start.promise;
    }

    return { start: start, status: status, setPlayback: function (brightness, intensity) {
        if (!current || current.state === 'stopping') return;
        current.controls = new Buffer([brightness, intensity]);
        if (current.canUpdate) current.child.stdin.write(current.controls);
    }, whenEnded: function () {
        return current ? current.end.promise : Promise.resolve(status());
    }, stop: function () {
        if (!current) { lastError = null; return Promise.resolve(status()); }
        var s = current;
        terminate(s, null);
        return s.end.promise;
    } };
};
