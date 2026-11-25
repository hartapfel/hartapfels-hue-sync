'use strict';
var frameSize = 84;
module.exports = function register(service, access, launch) {
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
                frames:current ? current.frames : 0});
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
        if (current) return reject(msg, -2, 'The previous capture session is still active.');
        var s = {token:msg.uniqueToken, frames:0}, pending = new Buffer(0);
        current = s;
        access(function (ok) {
            if (current !== s || s.stopping) return;
            if (!ok) {
                current = null;
                return reject(msg, 77, 'Grant Homebrew Channel capture permission to the Screen Sync capture service.');
            }
            try { s.child = launch(); }
            catch (err) { current = null; return reject(msg, -3, 'Could not launch native capture.'); }
            s.child.on('error', function () { /* close follows spawn failure */ });
            s.child.on('close', function () {
                clearTimeout(s.killTimer);
                if (current !== s) return;
                current = null;
                if (!s.stopping) reject(msg, -3, 'LG capture stopped. Check the video input and pause competing capture.');
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
                s.frames += end / frameSize;
                if (msg.respond({returnValue:true,subscribed:true,
                        zones:bytes.slice(end-frameSize,end).toString('base64')}) === false) stop(s);
            });
        });
    }, function (msg) {
        if (current && current.token === msg.uniqueToken) stop(current);
    });
};
