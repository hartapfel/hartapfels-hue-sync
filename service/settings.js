'use strict';
var fs = require('fs');
var path = require('path');
var crypto = require('crypto');

// LG documents /media/internal for data owned by a JS service. Keep the
// bridge credentials private to this service's UID across launches.
module.exports = function createSettings(root) {
    var dir = path.join(root, 'org.webosbrew.c9screensync');
    var file = path.join(dir, 'settings.json');
    function call(method, args) {
        return new Promise(function (resolve, reject) {
            fs[method].apply(fs, args.concat(function (err, result) {
                if (err) reject(err);
                else resolve(result);
            }));
        });
    }
    function directory() {
        return call('mkdir', [dir, 448]).catch(function (err) {
            if (err.code !== 'EEXIST') throw err;
        }).then(function () { return call('lstat', [dir]); }).then(function (stat) {
            if (!stat.isDirectory() || (process.getuid && (stat.uid !== process.getuid() || (stat.mode & 63)))) {
                throw new Error('Private service storage is unavailable.');
            }
        });
    }
    function writeAll(fd, buf, offset) {
        if (offset === buf.length) return Promise.resolve();
        return call('write', [fd, buf, offset, buf.length - offset, offset]).then(function (size) {
            if (!size) throw new Error('Could not write private service settings.');
            return writeAll(fd, buf, offset + size);
        });
    }
    return {
        load: function () {
            return directory().then(function () { return call('readFile', [file, 'utf8']); })
                .then(function (raw) {
                    var value = JSON.parse(raw);
                    if (!value || value.version !== 1) throw new Error('Unsupported saved settings.');
                    return value;
                }).catch(function (err) {
                    if (err.code === 'ENOENT') return { version: 1 };
                    throw err;
                });
        },
        save: function (value) {
            var temp = path.join(dir, 'settings-' + crypto.randomBytes(12).toString('hex'));
            var created = false;
            return directory().then(function () {
                return call('open', [temp, 'wx', 384]);
            }).then(function (fd) {
                created = true;
                var data = new Buffer(JSON.stringify(value), 'utf8');
                return writeAll(fd, data, 0).then(function () { return call('fsync', [fd]); })
                    .then(function () { return call('close', [fd]); }, function (err) {
                        return call('close', [fd]).then(function () { throw err; });
                    });
            }).then(function () { return call('rename', [temp, file]); }).catch(function (err) {
                if (!created) throw err;
                return call('unlink', [temp]).then(function () { throw err; }, function () { throw err; });
            });
        }
    };
};
