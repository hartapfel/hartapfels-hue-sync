'use strict';
(function () {
    var base = 'luna://org.webosbrew.c9screensync.service/';
    var state, pending = false, lastBridge = '', areaList = [], requests = [];
    function el(id) { return document.getElementById(id); }
    function call(method, params) {
        return new Promise(function (resolve, reject) {
            var bridge = new window.PalmServiceBridge();
            requests.push(bridge); // Keep the native bridge alive until it replies.
            var timer = setTimeout(function () {
                finish(); bridge.cancel(); reject(Error('TV service timed out.'));
            }, 65000);
            function finish() {
                clearTimeout(timer);
                var i = requests.indexOf(bridge);
                if (i !== -1) requests.splice(i, 1);
                bridge.onservicecallback = function () {};
            }
            bridge.onservicecallback = function (raw) {
                finish();
                try {
                    var reply = JSON.parse(raw);
                    if (!reply.returnValue) throw Error(reply.errorText || 'TV service unavailable.');
                    resolve(reply.result);
                } catch (err) { reject(err); }
            };
            try { bridge.call(base + method, JSON.stringify(params || {})); }
            catch (err) { finish(); reject(err); }
        });
    }
    function render() {
        var s = state || {}, idle = s.state === 'idle', live = s.state === 'streaming';
        var labels = { idle: s.area ? 'Ready to sync' : 'Set up screen sync', starting: 'Starting sync…',
            streaming: 'Syncing', stopping: 'Stopping sync…' };
        el('state').textContent = labels[s.state] || 'Connecting…';
        el('room').textContent = (s.area ? s.area.name + ' · ' : '') + 'Video · Moderate';
        el('sync').textContent = live ? 'Stop sync' : 'Start sync';
        el('sync').disabled = pending || !(live || (idle && s.area));
        el('pair').disabled = el('host').disabled = pending || !idle;
        el('reload').disabled = pending || !idle || !s.paired;
        el('boot').disabled = pending || (!s.autoStart && !s.area);
        el('boot').setAttribute('aria-pressed', String(!!s.autoStart));
        el('boot').textContent = 'Start sync on power-on · ' + (s.autoStart ? 'On' : 'Off');
        ['piccap', 'direct'].forEach(function (source) {
            el(source).disabled = pending || !idle;
            el(source).setAttribute('aria-pressed', String(s.source === source));
        });
        el('sourceHelp').textContent = s.source === 'piccap' ?
            'In PicCap, turn off Unix socket mode and send video to 127.0.0.1 on port 19401. Leave PicCap capture running.' :
            s.source === 'direct' ? 'If PicCap is capturing, pause it so this app can use the TV capture device.' : '';
        el('pairHelp').textContent = s.bridge ? 'Paired with ' + s.bridge.name + ' (' + s.bridge.host + ').' :
            'Press the link button on your Hue Bridge, then choose Pair bridge.';
        if (s.bridge && s.bridge.host !== lastBridge) {
            el('host').value = lastBridge = s.bridge.host;
        }
        Array.prototype.forEach.call(el('areas').children, function (btn) {
            btn.disabled = pending || !idle;
            btn.setAttribute('aria-pressed', String(!!s.area && s.area.id === btn.value));
        });
    }
    function showAreas(list) {
        areaList = list;
        var box = el('areas');
        box.textContent = '';
        list.forEach(function (area) {
            var button = document.createElement('button');
            button.value = area.id;
            button.textContent = area.name;
            button.onclick = function () {
                action(function () { return call('select', {id: area.id}).then(update); }, button);
            };
            box.appendChild(button);
        });
        if (!list.length) box.textContent = 'Create an entertainment area in the Hue app, then load areas again.';
        render();
    }
    function update(value) { state = value; render(); }
    function reload() { return call('areas').then(showAreas); }
    function action(work, origin) {
        if (pending) return;
        pending = true;
        el('feedback').textContent = '';
        render();
        Promise.resolve().then(work).catch(function (err) {
            el('feedback').textContent = err.message;
        }).then(function () {
            pending = false; render();
            if (origin && !origin.disabled && document.activeElement === document.body) origin.focus();
        });
    }
    el('pair').onclick = function () {
        action(function () {
            return call('pair', {host: el('host').value.trim()}).then(update).then(reload);
        }, el('pair'));
    };
    el('reload').onclick = function () { action(reload, el('reload')); };
    ['piccap', 'direct'].forEach(function (source) {
        el(source).onclick = function () {
            action(function () { return call('setSource', {source: source}).then(update); }, el(source));
        };
    });
    el('boot').onclick = function () {
        action(function () { return call('setAutoStart', {enabled: !state.autoStart}).then(update); }, el('boot'));
    };
    el('sync').onclick = function () {
        var method = state.state === 'streaming' ? 'stop' : 'start';
        action(function () { return call(method).then(function () { return call('status'); }).then(update); }, el('sync'));
    };
    // Directional navigation also works on TVs with the Magic Remote pointer hidden.
    document.addEventListener('keydown', function (event) {
        var key = event.keyCode, node = document.activeElement;
        if (key < 37 || key > 40 || (node === el('host') && (key === 37 || key === 39))) return;
        var controls = Array.prototype.slice.call(document.querySelectorAll('button:enabled,input:enabled'));
        if (!controls.length) return;
        var index = controls.indexOf(node);
        var direction = key === 37 || key === 38 ? -1 : 1;
        controls[Math.max(0, Math.min(controls.length - 1, index + direction))].focus();
        event.preventDefault();
    });
    var refreshing = false;
    function refresh() {
        if (pending || refreshing || document.hidden) return;
        refreshing = true;
        call('status').then(function (s) {
            if (pending) return;
            var first = !state;
            update(s);
            if (s.error) el('feedback').textContent = s.error;
            if (first) {
                (s.area ? el('sync') : s.paired ? el('reload') : el('host')).focus();
                if (s.paired && !areaList.length) return reload();
            }
        }).catch(function (err) { el('feedback').textContent = err.message; })
            .then(function () { refreshing = false; });
    }
    render(); refresh();
    document.addEventListener('visibilitychange', refresh);
    setInterval(refresh, 4000);
}());
