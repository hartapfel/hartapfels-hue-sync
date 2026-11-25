'use strict';

// webOS 4.x supplies Node 0.12. Keep this service module compatible with ES5.
var fs = require('fs');
var https = require('https');
var net = require('net');
var path = require('path');
var roots = fs.readFileSync(path.join(__dirname, 'hue-roots.pem'), 'ascii')
    .match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g);
var bridgePattern = /^[0-9a-f]{16}$/i;
var uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
var resource = '/clip/v2/resource/entertainment_configuration';

function bridgeError(code, message) {
    var error = new Error(message);
    error.code = code;
    return error;
}

function request(host, bridgeId, key, method, route, body) {
    return new Promise(function (resolve, reject) {
        if (!net.isIP(host) || (bridgeId && !bridgePattern.test(bridgeId)) || (key && !bridgeId)) {
            throw new Error('A bridge IP and its verified identity are required.');
        }
        var headers = { Accept: 'application/json' };
        if (key) headers['hue-application-key'] = key;
        var payload = body ? JSON.stringify(body) : '';
        if (body) {
            headers['Content-Type'] = 'application/json';
            headers['Content-Length'] = Buffer.byteLength(payload);
        }
        var verifiedId;
        var req = https.request({
            host: host, method: method, path: route, agent: false, headers: headers,
            ca: roots, secureProtocol: 'TLSv1_2_method', rejectUnauthorized: true,
            checkServerIdentity: function (_, cert) {
                var cn = cert.subject && cert.subject.CN;
                if (typeof cn !== 'string' || !bridgePattern.test(cn) ||
                        (bridgeId && cn.toLowerCase() !== bridgeId.toLowerCase())) {
                    return new Error('The certificate does not identify the selected Hue Bridge.');
                }
                verifiedId = cn.toLowerCase();
            }
        }, function (res) {
            var chunks = [];
            var size = 0;
            res.on('error', fail);
            res.on('aborted', function () { fail(bridgeError('ECONNRESET', 'Bridge response interrupted.')); });
            res.on('data', function (chunk) {
                size += chunk.length;
                if (size > 1024 * 1024) {
                    fail(new Error('Bridge response exceeded the size limit.'));
                    req.abort();
                } else {
                    chunks.push(chunk);
                }
            });
            res.on('end', function () {
                clearTimeout(deadline);
                if (res.statusCode < 200 || res.statusCode >= 300) {
                    return reject(bridgeError('HTTP_' + res.statusCode, 'Bridge returned HTTP ' + res.statusCode));
                }
                try {
                    var json = size && route !== '/auth/v1' ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : null;
                    resolve({ body: json, headers: res.headers, bridgeId: verifiedId });
                } catch (err) {
                    reject(new Error('Bridge returned malformed JSON.'));
                }
            });
        });
        function fail(err) {
            clearTimeout(deadline);
            reject(err);
        }
        var deadline = setTimeout(function () {
            fail(bridgeError('ETIMEDOUT', 'Hue Bridge request timed out.'));
            req.abort();
        }, 5000);
        req.on('error', fail);
        req.end(payload);
    });
}

function identify(host) {
    return request(host, null, null, 'GET', '/api/config').then(function (reply) {
        var cfg = reply.body;
        if (!cfg || !reply.bridgeId || String(cfg.bridgeid).toLowerCase() !== reply.bridgeId) {
            throw new Error('Bridge identity does not match its certificate.');
        }
        return { host: host, id: reply.bridgeId, name: cfg.name, model: cfg.modelid };
    });
}

function pair(bridge) {
    if (!bridge || !bridgePattern.test(bridge.id)) return Promise.reject(new Error('Identify the bridge first.'));
    return request(bridge.host, bridge.id, null, 'POST', '/api', {
        devicetype: 'c9_screen_sync#lg_tv', generateclientkey: true
    }).then(function (reply) {
        var entry = Array.isArray(reply.body) && reply.body[0];
        if (entry && entry.error && entry.error.type === 101) {
            throw bridgeError('LINK_BUTTON', 'Press the link button on your Hue Bridge, then pair again.');
        }
        var keys = entry && entry.success;
        if (!keys || typeof keys.username !== 'string' || !/^[!-~]{1,128}$/.test(keys.username) ||
                typeof keys.clientkey !== 'string' || !/^[0-9a-f]{32}$/i.test(keys.clientkey)) {
            throw new Error('Bridge did not return a streaming credential.');
        }
        return { applicationKey: keys.username, clientKey: keys.clientkey };
    });
}

function data(reply) {
    var body = reply.body;
    if (!body || !Array.isArray(body.errors) || body.errors.length || !Array.isArray(body.data)) {
        throw new Error('Hue Bridge declined the entertainment request.');
    }
    return body.data;
}

function areaInfo(area) {
    if (!area || area.type !== 'entertainment_configuration' || !uuid.test(area.id) ||
            (area.status !== 'active' && area.status !== 'inactive')) {
        throw new Error('Invalid entertainment area returned by Hue Bridge.');
    }
    return { id: area.id, name: (area.metadata && area.metadata.name) || area.name || area.id,
        status: area.status, owner: area.active_streamer && area.active_streamer.rid || null };
}

function areaLayout(area) {
    var result = areaInfo(area);
    if (!Array.isArray(area.channels) || !area.channels.length || area.channels.length > 256) {
        throw new Error('The selected area has no usable channels.');
    }
    var seen = {};
    result.channels = area.channels.map(function (channel) {
        var id = channel && channel.channel_id;
        var p = channel && channel.position;
        if (typeof id !== 'number' || id !== Math.floor(id) || id < 0 || id > 255 || seen[id] ||
                !p || ![p.x, p.y, p.z].every(function (v) { return typeof v === 'number' && v >= -1 && v <= 1; })) {
            throw new Error('Invalid channel ID or position in the selected area.');
        }
        seen[id] = true;
        return { id: id, x: p.x, y: p.y, z: p.z };
    });
    return result;
}

function connect(bridge, applicationKey) {
    if (!bridge || !bridgePattern.test(bridge.id) || !applicationKey) {
        throw new Error('Pair with the selected Hue Bridge first.');
    }
    function call(method, route, body) {
        return request(bridge.host, bridge.id, applicationKey, method, route, body);
    }
    function selected(id, includeLayout) {
        if (!uuid.test(id)) return Promise.reject(new Error('Invalid entertainment area ID.'));
        return call('GET', resource + '/' + id).then(data).then(function (items) {
            if (items.length !== 1 || items[0].id !== id) throw new Error('The selected area is unavailable.');
            return includeLayout ? areaLayout(items[0]) : areaInfo(items[0]);
        });
    }
    function applicationId() {
        return call('GET', '/auth/v1').then(function (reply) {
            var id = reply.headers['hue-application-id'];
            if (!uuid.test(id)) throw new Error('Bridge did not identify this paired application.');
            return id;
        });
    }
    function change(id, start) {
        return Promise.all([applicationId(), selected(id, start)]).then(function (values) {
            var owner = values[0], area = values[1];
            if (area.status === 'active' && area.owner !== owner) {
                if (!start) return;
                throw bridgeError('AREA_BUSY', 'Another application is using this entertainment area.');
            }
            if (start === (area.status === 'active')) return area;
            return call('PUT', resource + '/' + id, { action: start ? 'start' : 'stop' }).then(data)
                .then(function () { return start ? confirmStart(id, owner, 8) : undefined; });
        });
    }
    function confirmStart(id, owner, retries) {
        return selected(id, true).then(function (area) {
            if (area.status === 'active' && area.owner === owner) return area;
            if (area.status === 'active' && area.owner && area.owner !== owner)
                throw bridgeError('AREA_BUSY', 'Another application is using this entertainment area.');
            if (!retries) throw new Error('Bridge did not confirm stream ownership.');
            return new Promise(function (resolve) { setTimeout(resolve, 200); })
                .then(function () { return confirmStart(id, owner, retries - 1); });
        });
    }
    return {
        list: function () { return call('GET', resource).then(data).then(function (items) { return items.map(areaInfo); }); },
        layout: function (id) { return selected(id, true); },
        start: function (id) { return change(id, true); },
        stop: function (id) { return change(id, false); }
    };
}

module.exports = { identify: identify, pair: pair, connect: connect };
