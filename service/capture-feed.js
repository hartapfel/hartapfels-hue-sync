'use strict';
var base = 'luna://org.webosbrew.c9screensync.capture/';
module.exports = function captureFeed(service) {
    return {
        access: function () {
            return new Promise(function (resolve) {
                // A pending LS2 call alone does not keep the C9's Node loop alive.
                // Treat a lost status reply as unknown; the frame subscription
                // still enforces device permission when starting capture.
                var deadline = setTimeout(function () { resolve(null); }, 5000);
                service.call(base + 'status', {}, function (msg) {
                    clearTimeout(deadline);
                    var p = msg.payload || {};
                    resolve(p.returnValue ? p.deviceAccess : null);
                });
            });
        },
        stream: function (pipe, failed, mode) {
            var sub = service.subscribe(base + 'frames', {subscribe:true,mode:mode || 'video'});
            var closed = false, blocked = false;
            pipe.on('drain', function () { blocked = false; });
            function lost(message, code) {
                if (closed) return;
                var error = Error(message);
                error.code = code;
                failed(error);
            }
            pipe.on('error', function () { lost('Lost the zone pipe to the Hue worker.', 'VIDEO_NOT_READY'); });
            sub.on('response', function (msg) {
                if (closed) return;
                var p = msg.payload || {};
                if (!p.returnValue) return lost(p.errorText || 'Native capture service unavailable.',
                    p.errorCode === 77 ? 'CAPTURE_ACCESS_DENIED' : 'VIDEO_NOT_READY');
                if (typeof p.zones !== 'string' || !/^[a-zA-Z0-9+/]{112}$/.test(p.zones))
                    return lost('The capture service returned an invalid zone sample.', 'VIDEO_NOT_READY');
                if (!blocked) blocked = !pipe.write(new Buffer(p.zones, 'base64'));
            });
            sub.on('cancel', function () { lost('The native capture session ended.', 'VIDEO_NOT_READY'); });
            return function () { closed = true; sub.cancel(); pipe.end(); };
        }
    };
};
