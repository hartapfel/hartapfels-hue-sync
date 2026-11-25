'use strict';
var assert = require('assert');
var fs = require('fs');
var path = require('path');
var os = require('os');
var crypto = require('crypto');
var settings = require('../service/settings');
var dir = path.join(os.tmpdir(), 'c9-settings-' + crypto.randomBytes(12).toString('hex'));
fs.mkdirSync(dir, 448);
var store = settings(dir);
var privateDir = path.join(dir, 'org.webosbrew.c9screensync');
var filename = path.join(privateDir, 'settings.json');
var config = { version: 1, bridge: { name: 'Cinema' }, area: 'fixture' };

setImmediate(function () {
    store.load().then(function (empty) {
        assert.deepEqual(empty, { version: 1 });
        return store.save(config);
    }).then(function () { return settings(dir).load(); }).then(function (saved) {
        assert.deepEqual(saved, config);
        config.area = 'other fixture';
        return store.save(config);
    }).then(function () { return store.load(); }).then(function (saved) {
        assert.deepEqual(saved, config);
        assert.deepEqual(fs.readdirSync(privateDir), ['settings.json']);
        if (process.getuid) {
            assert.equal(fs.statSync(privateDir).mode & 511, 448);
            assert.equal(fs.statSync(filename).mode & 511, 384);
        }
        console.log('Saved, reopened, and atomically replaced service settings on ' + process.version);
    }).catch(function (err) { console.error(err.stack); process.exitCode = 1; }).then(function () {
        if (fs.existsSync(filename)) fs.unlinkSync(filename);
        fs.rmdirSync(privateDir);
        fs.rmdirSync(dir);
    });
});
