'use strict';
var Service = require('webos-service');
var service = new Service(require('./package.json').name);
var executable = require('path').join(__dirname, 'c9-capture-zones');
var fs = require('fs');
require('./capture')(service, function (done) {
    fs.access('/dev/mem', fs.R_OK | fs.W_OK, function (err) { done(!err); });
}, function () {
    return require('child_process').spawn(executable, [], {stdio:['ignore','ignore','ignore','pipe']});
});
