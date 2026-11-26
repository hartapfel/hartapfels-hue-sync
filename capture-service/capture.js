'use strict';
var frameSize = 84;
module.exports = function register(service, access, launch, power) {
    var current;
    function allowed(message) {
        return message.sender === 'org.webosbrew.c9screensync.service';
    }
    function reject(message, code, reason) {
        var reply = {returnValue:false, errorCode:code, errorText:reason};
        if (message.isSubscription) message.cancel(reply);
        else message.respond(reply);
    }
    service.register('status', function (msg) {
        // No keys, image samples or addresses in this diagnostic.
        access(function (ok) {
            msg.respond({returnValue:true, deviceAccess:ok, active:!!(current && current.child),
                frames:current ? current.frames : 0, changedFrames:current ? current.changed : 0,
                graphicsVisible:current ? current.graphics : null, power:power ? power() : null});
        });
    });
    function stop(s) {
        if (!s || s.stopping) return;
        s.stopping = true;
        if (!s.child) { current = null; return; }
        s.child.kill();
        s.killTimer = setTimeout(function () { s.child.kill('SIGKILL'); }, 1500);
    }
    service.register('frames', function (msg) {
        if (!allowed(msg) || !msg.isSubscription)
            return reject(msg, -1, 'Capture requires a Screen Sync session.');
        var mode = msg.payload && msg.payload.mode || 'video';
        if (['video','gaming','music'].indexOf(mode) < 0) return reject(msg, -1, 'Invalid capture mode.');
        if (current) return reject(msg, -2, 'The previous capture session is still active.');
        var s = {token:msg.uniqueToken, frames:0, changed:0, graphics:null}, pending = new Buffer(0);
        current = s;
        access(function (ok) {
            if (current !== s || s.stopping) return;
            if (!ok) {
                current = null;
                return reject(msg, 77, 'Grant Homebrew Channel capture permission to the Screen Sync capture service.');
            }
            try { s.child = launch(mode); }
            catch (err) { current = null; return reject(msg, -3, 'Could not launch native capture.'); }
            if (s.child.stderr) {
                require('readline').createInterface({input:s.child.stderr}).on('line', function (line) {
                    if (/^graphics-visible=[01]$/.test(line)) s.graphics = line.slice(-1) === '1';
                });
            }
            s.child.on('error', function () { /* close follows spawn failure */ });
            s.child.on('close', function () {
                clearTimeout(s.killTimer);
                if (current !== s) return;
                current = null;
                if (!s.stopping) reject(msg, -3, mode === 'music' ? 'TV playback audio capture stopped or is unavailable.' : 'LG capture stopped. Check the video input and pause competing capture.');
            });
            s.child.stdio[3].on('error', function () {
                reject(msg, -3, 'Lost the native capture pipe.');
                stop(s);
            });
            s.child.stdio[3].on('data', function (chunk) {
                if (s.stopping) return;
                var bytes = pending.length ? Buffer.concat([pending,chunk]) : chunk;
                var end = bytes.length - bytes.length % frameSize;
                pending = bytes.slice(end);
                if (!end) return;
                var latest = bytes.slice(end-frameSize,end);
                if (s.previous && Buffer.compare(s.previous, latest) !== 0) ++s.changed;
                s.previous = latest;
                s.frames += end / frameSize;
                if (msg.respond({returnValue:true,subscribed:true,
                        zones:bytes.slice(end-frameSize,end).toString('base64')}) === false) stop(s);
            });
        });
    }, function (msg) {
        if (current && current.token === msg.uniqueToken) stop(current);
    });
};
