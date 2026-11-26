'use strict';

// Relay only the display state from the capture service's webOS power subscription.
module.exports = function registerPower(service) {
    var watch, state = 'idle';
    function cancel() {
        var old = watch;
        watch = null;
        state = 'idle';
        if (old) old.sub.cancel();
    }
    service.register('watchPower', function (msg) {
        if (msg.sender !== 'org.webosbrew.c9screensync.service' || !msg.isSubscription || watch)
            return msg.respond({returnValue:false,errorText:'Power monitoring requires a Screen Sync session.'});
        var sub = service.subscribe('luna://com.webos.service.tvpower/power/getPowerState', {subscribe:true});
        watch = {token:msg.uniqueToken, sub:sub};
        state = 'waiting';
        sub.on('response', function (reply) {
            if (!watch || watch.sub !== sub) return;
            var p = reply.payload || {};
            if (!p.returnValue) {
                cancel();
                state = 'unavailable';
                return msg.cancel({returnValue:false,errorText:'TV power updates unavailable.'});
            }
            var off = p.onOff === 'off' || /^(Active Standby|Suspend|Power Off|Screen Off)$/i.test(p.state) ||
                /(?:power.?off|standby|suspend|screen.?off)/i.test(p.processing || '');
            if (!off && p.state !== 'Active') return;
            state = off ? 'standby' : 'active';
            if (msg.respond({returnValue:true, subscribed:true, standby:off}) === false) cancel();
        });
        sub.on('cancel', function () {
            if (!watch || watch.sub !== sub) return;
            watch = null;
            state = 'unavailable';
            msg.cancel({returnValue:false,errorText:'TV power subscription ended.'});
        });
    }, function (msg) {
        if (watch && watch.token === msg.uniqueToken) cancel();
    });
    return function () { return state; };
};
