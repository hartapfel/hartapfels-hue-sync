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
        return spawn(path.join(__dirname, 'c9-sync'), [], { stdio: ['pipe', 'pipe', 'pipe'] });
    };

    function status() {
        return { state: current ? current.state : 'idle', error: lastError };
    }

    function finish(s, error) {
        if (s.finishing) return;
        s.finishing = true;
        clearTimeout(s.deadline);
        clearTimeout(s.killTimer);
        s.state = 'stopping';
        function done(releaseError) {
            var reason = releaseError || error;
            lastError = reason ? reason.message : null;
            current = null;
            if (!s.ready) s.start.reject(reason || new Error('Start cancelled.'));
            s.end.resolve(status());
        }
        // A timed-out start request can still have reached the bridge. stop()
        // checks the active identity before issuing a release.
        Promise.resolve().then(function () { return s.client.stop(s.area); })
            .then(function () { done(null); }, done);
    }

    function terminate(s, error) {
        if (s.finishing || s.state === 'stopping') return;
        s.state = 'stopping';
        s.failure = error;
        clearTimeout(s.deadline);
        if (s.child) {
            s.child.kill();
            s.killTimer = setTimeout(function () { s.child.kill('SIGKILL'); }, 2000);
        }
    }

    function start(client, bridge, keys, area, capture) {
        if (current) return Promise.reject(new Error('A screen sync session is already in progress.'));
        var s = { state: 'starting', area: area, client: client, start: deferred(), end: deferred() };
        current = s;
        lastError = null;
        Promise.resolve().then(function () { return client.start(area); }).then(function (layout) {
            if (s.state === 'stopping') return finish(s, s.failure);
            var descriptor = encode(bridge, keys, layout, capture, 2100);
            s.child = launch();
            var pending = '', diagnostic = '';
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
                        /PicCap input/i.test(diagnostic) ? 'PicCap input unavailable. Set PicCap to TCP 127.0.0.1 port 19401 and start capture.' :
                        /capture failed|No video frame|video backend/i.test(diagnostic) ?
                            'TV capture failed. Check the video input and whether PicCap is using capture.' :
                            'The screen capture worker exited.';
                    error = new Error(message);
                }
                finish(s, error);
            });
            s.child.stdin.on('error', function () {
                terminate(s, new Error('Could not configure the screen capture worker.'));
            });
            s.child.stdout.setEncoding('utf8');
            s.child.stdout.on('data', function (chunk) {
                if (s.state !== 'starting') return;
                pending += chunk;
                if (pending.length > 64) return terminate(s, new Error('Invalid worker response.'));
                if (pending.replace(/\r\n/g, '\n') === 'streaming\n') {
                    clearTimeout(s.deadline);
                    s.state = 'streaming';
                    s.ready = true;
                    s.start.resolve(status());
                }
            });
            s.deadline = setTimeout(function () {
                terminate(s, new Error('The first lighting frame timed out.'));
            }, 15000);
            s.child.stdin.end(descriptor, function () { descriptor.fill(0); });
        }).catch(function (err) {
            if (s.child) terminate(s, err);
            else finish(s, s.state === 'stopping' ? s.failure : err);
        });
        return s.start.promise;
    }

    return { start: start, status: status, whenEnded: function () {
        return current ? current.end.promise : Promise.resolve(status());
    }, stop: function () {
        if (!current) return Promise.resolve(status());
        var s = current;
        terminate(s, null);
        return s.end.promise;
    } };
};
