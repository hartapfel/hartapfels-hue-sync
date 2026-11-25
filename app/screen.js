'use strict';
(function () {
    var base = 'luna://org.webosbrew.c9screensync.service/';
    var state, pending = false, lastBridge = '', areaList = [], requests = [];
    function el(id) { return document.getElementById(id); }
    var levels = ['Subtle', 'Moderate', 'High', 'Intense'];
    var brightness = 100, intensity = 1, saving = false, saveTimer, editVersion = 0;
    var levelButtons = el('intensities').querySelectorAll('button');
    function renderControls() {
        el('brightness').value = brightness;
        el('brightnessValue').textContent = brightness + '%';
        el('brightness').disabled = !state;
        Array.prototype.forEach.call(levelButtons, function (btn, i) {
            btn.disabled = !state;
            btn.setAttribute('aria-pressed', String(i === intensity));
        });
        el('room').textContent = (state && state.area ? state.area.name + ' · ' : '') + 'Video · ' + levels[intensity];
    }
    function saveControls() {
        clearTimeout(saveTimer); saveTimer = null;
        if (saving) return;
        var version = editVersion;
        saving = true;
        call('setPlayback', {brightness:brightness, intensity:intensity}).then(function (s) {
            state.brightness = s.brightness; state.intensity = s.intensity;
        }, function (err) {
            el('feedback').textContent = err.message;
            if (version === editVersion) {
                brightness = state.brightness; intensity = state.intensity; renderControls();
            }
        }).then(function () {
            saving = false;
            if (version !== editVersion) saveControls();
        });
    }
    function changeControls() {
        ++editVersion;
        renderControls();
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveControls, 100);
    }
    el('brightness').oninput = function () { brightness = Number(this.value); changeControls(); };
    el('brightness').onchange = function () {
        if (brightness !== Number(this.value)) { brightness = Number(this.value); changeControls(); }
    };
    Array.prototype.forEach.call(levelButtons, function (btn, i) {
        btn.onclick = function () { intensity = i; changeControls(); };
    });
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
        var waiting = idle && s.wakeRetry;
        var canStop = live || waiting || s.state === 'starting';
        var directDenied = s.source === 'direct' && s.directAccess === false;
        el('state').textContent = idle && directDenied ? 'Direct capture needs permission' :
            waiting ? 'Waiting to retry sync…' : labels[s.state] || 'Connecting…';
        renderControls();
        el('sync').textContent = canStop ? 'Stop sync' : 'Start sync';
        el('sync').disabled = pending || !(canStop || (idle && s.area && !directDenied));
        el('pair').disabled = el('host').disabled = pending || !idle;
        el('reload').disabled = pending || !idle || !s.paired;
        el('boot').disabled = pending || (!s.autoStart && !s.area);
        el('boot').setAttribute('aria-pressed', String(!!s.autoStart));
        el('boot').textContent = (s.coldBootOnly ? 'Start sync on cold boot' : 'Start sync on power-on') +
            ' · ' + (s.autoStart ? 'On' : 'Off');
        ['piccap', 'direct'].forEach(function (source) {
            el(source).disabled = pending || !idle;
            el(source).setAttribute('aria-pressed', String(s.source === source));
        });
        el('sourceHelp').textContent = s.source === 'piccap' ?
            'In PicCap, turn off Unix socket mode and send video to 127.0.0.1 on port 19401. Leave PicCap capture running.' :
            s.source === 'direct' && s.directAccess === false ?
                'Grant Homebrew Channel permission to the Screen Sync capture service. The status updates when capture is available.' :
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
    function update(value) {
        state = value;
        if (!saving && !saveTimer) {
            brightness = value.brightness === undefined ? 100 : value.brightness;
            intensity = value.intensity === undefined ? 1 : value.intensity;
        }
        render();
    }
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
        var method = state.wakeRetry || state.state === 'starting' || state.state === 'streaming' ? 'stop' : 'start';
        action(function () { return call(method).then(function () { return call('status'); }).then(update); }, el('sync'));
    };
    // Directional navigation also works on TVs with the Magic Remote pointer hidden.
    var viewport = document.querySelector('main');
    function showFocus(node) {
        var bounds = node.getBoundingClientRect(), box = viewport.getBoundingClientRect();
        if (bounds.bottom > box.bottom - 12) viewport.scrollTop += bounds.bottom - box.bottom + 12;
        else if (bounds.top < box.top + 12) viewport.scrollTop += bounds.top - box.top - 12;
    }
    document.addEventListener('focus', function (e) {
        if (viewport.contains(e.target) && e.target !== viewport) showFocus(e.target);
    }, true);
    viewport.addEventListener('wheel', function (e) {
        if (!e.deltaY) return;
        viewport.scrollTop += e.deltaY * (e.deltaMode === 1 ? 32 : e.deltaMode === 2 ? viewport.clientHeight : 1);
        e.preventDefault();
    }, false);
    document.addEventListener('keydown', function (event) {
        var key = event.keyCode, node = document.activeElement;
        if (node === el('brightness') && (key === 37 || key === 39)) {
            var next = Math.max(0, Math.min(100, brightness + (key === 37 ? -1 : 1)));
            if (next !== brightness) { brightness = next; changeControls(); }
            event.preventDefault();
            return;
        }
        if (key < 37 || key > 40 || (node.tagName === 'INPUT' && (key === 37 || key === 39))) return;
        var controls = Array.prototype.slice.call(document.querySelectorAll('button:enabled,input:enabled'));
        if (!controls.length) return;
        var index = controls.indexOf(node);
        var direction = key === 37 || key === 38 ? -1 : 1;
        var target = controls[Math.max(0, Math.min(controls.length - 1, index + direction))];
        if (target === node) viewport.scrollTop = direction > 0 ? viewport.scrollHeight : 0;
        else { target.focus(); showFocus(target); }
        event.preventDefault();
    });
    var refreshing = false;
    function refresh() {
        if (pending || refreshing || document.hidden) return;
        refreshing = true;
        var version = editVersion;
        call('status').then(function (s) {
            if (pending || version !== editVersion || saving || saveTimer) return;
            var first = !state;
            if (state && state.error && !s.error && el('feedback').textContent === state.error) el('feedback').textContent = '';
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
    function flushControls() { if (saveTimer) saveControls(); }
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) flushControls(); else refresh();
    });
    window.addEventListener('pagehide', flushControls);
    setInterval(refresh, 4000);
}());
