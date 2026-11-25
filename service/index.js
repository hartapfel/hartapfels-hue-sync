'use strict';
var Service = require('webos-service');
var service = new Service(require('./package.json').name);
var feed = require('./capture-feed')(service);
var session = require('./sync-session')(null, feed);
var store = require('./settings')('/media/internal');
var app = require('./controller')(require('./hue-bridge'), store, session, feed.access);
require('./methods')(service, app, session);
