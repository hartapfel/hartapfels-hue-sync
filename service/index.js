'use strict';
var Service = require('webos-service');
var service = new Service(require('./package.json').name);
var session = require('./sync-session')();
var store = require('./settings')('/media/internal');
var app = require('./controller')(require('./hue-bridge'), store, session);
require('./methods')(service, app, session);
