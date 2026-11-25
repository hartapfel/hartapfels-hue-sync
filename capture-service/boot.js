'use strict';

// Register the app's fixed boot action on TVs where the ordinary service
// cannot subscribe to the core-boot-done signal. The user controls it in the UI.
module.exports = function registerBoot(service) {
    var main = 'org.webosbrew.c9screensync.service';
    var manager = 'luna://com.palm.activitymanager/';
    var name = 'C9 Screen Sync after core boot';
    service.register('scheduleBoot', function (msg) {
        var p = msg.payload || {};
        if (msg.sender !== main || typeof p.enabled !== 'boolean' ||
            (p.enabled && (typeof p.registration !== 'string' || !/^[a-f0-9]{32}$/.test(p.registration)))) {
            return msg.respond({returnValue:false, errorText:'Use the Screen Sync startup setting.'});
        }
        var spec = {name:name, description:'Start the selected Screen Sync session after TV boot',
            type:{foreground:true, persist:true, continuous:true, explicit:true, power:false},
            trigger:{method:'luna://com.webos.bootManager/getBootStatus', params:{subscribe:true},
                where:{prop:['signals','core-boot-done'], op:'=', val:true}},
            callback:{method:'luna://' + main + '/onPowerOn',
                params:{coldBootOnly:true, registration:p.registration, coreBoot:true}}};
        var action = p.enabled ? 'create' : 'cancel';
        var args = p.enabled ? {activity:spec, start:true, replace:true, subscribe:false} : {activityName:name};
        service.call(manager + action, args, function (r) {
            var result = r.payload || {};
            if (result.returnValue || (!p.enabled && result.errorCode === 2)) {
                msg.respond({returnValue:true, activityId:result.activityId});
            } else msg.respond({returnValue:false, errorText:result.errorText || 'Could not update the boot activity.'});
        });
    });
};
