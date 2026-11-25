"""Drive the actual TV setup screen in headless Chromium with a simulated Luna bus."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = """
<script>
var state = {state:'idle', paired:false, area:null, bridge:null, autoStart:false, source:'piccap', directAccess:false};
var area = {id:'cinema', name:'Living room'};
function PalmServiceBridge() {}
PalmServiceBridge.prototype.cancel = function () {};
PalmServiceBridge.prototype.call = function (uri, raw) {
    var self = this, method = uri.split('/').pop(), p = JSON.parse(raw), result = state;
    if (method === 'pair') {
        if (p.host !== '127.0.0.1') throw Error('Wrong bridge IP');
        state.paired = true; state.bridge = {name:'Test bridge',host:p.host};
    } else if (method === 'areas') result = [area];
    else if (method === 'select') { if (p.id !== area.id) throw Error('Wrong area'); state.area = area; }
    else if (method === 'start') state.state = 'streaming';
    else if (method === 'stop') { state.state = 'idle'; state.wakeRetry = false; }
    else if (method === 'setAutoStart') { state.autoStart = p.enabled; state.coldBootOnly = p.enabled; }
    else if (method === 'setSource') state.source = p.source;
    else if (method === 'setPlayback') { state.brightness = p.brightness; state.intensity = p.intensity; }
    else if (method !== 'status') throw Error('Unexpected call ' + method);
    var response = JSON.stringify({returnValue:true,result:result});
    setTimeout(function () { self.onservicecallback(response); }, 1);
};
</script>
"""
CHECK = """
<script>
var byId = function(id) { return document.getElementById(id); };
var check = function(ok, why) { if (!ok) throw Error(why); };
var steps = [
    function () {
        check(document.activeElement === byId('host'), 'Initial remote focus');
        check(byId('brightness').value === '100' && byId('room').textContent.indexOf('Moderate') >= 0, 'Default playback');
        check(byId('sync').disabled, 'Missing area gating');
        byId('host').value = '127.0.0.1';
        document.dispatchEvent(new KeyboardEvent('keydown', {keyCode:40,bubbles:true}));
        check(document.activeElement === byId('pair'), 'D-pad navigation');
        byId('pair').click();
    },
    function () {
        check(byId('pairHelp').textContent.indexOf('Test bridge') >= 0, 'Bridge label');
        check(byId('areas').firstChild.textContent === 'Living room', 'Area import');
        byId('areas').firstChild.click();
    },
    function () {
        check(!byId('sync').disabled, 'Area selection');
        check(byId('piccap').getAttribute('aria-pressed') === 'true', 'Initial PicCap input');
        byId('direct').click();
    },
    function () {
        check(byId('direct').getAttribute('aria-pressed') === 'true', 'Direct video input');
        check(byId('sourceHelp').textContent.indexOf('Grant Homebrew') >= 0, 'Device permission feedback');
        check(byId('sync').disabled && byId('state').textContent.indexOf('permission') >= 0, 'Unavailable input gating');
        byId('piccap').click();
    },
    function () {
        check(byId('sourceHelp').textContent.indexOf('19401') >= 0, 'Local PicCap endpoint');
        var view = document.querySelector('main');
        view.scrollTop = 0;
        view.dispatchEvent(new WheelEvent('wheel', {deltaY:120, bubbles:true, cancelable:true}));
        check(view.scrollTop > 0, 'Wheel scroll');
        byId('brightness').focus();
        var left = new KeyboardEvent('keydown', {keyCode:37,bubbles:true,cancelable:true});
        byId('brightness').dispatchEvent(left);
        check(left.defaultPrevented && document.activeElement === byId('brightness') && byId('brightness').value === '99', 'Slider arrow keys');
        for (var i=0; i<20; ++i) document.dispatchEvent(new KeyboardEvent('keydown', {keyCode:40,bubbles:true}));
        check(document.activeElement === byId('boot') && view.scrollTop + view.clientHeight >= view.scrollHeight - 1, 'Reach end with remote');
        byId('boot').click();
    },
    function () {
        check(byId('boot').getAttribute('aria-pressed') === 'true', 'Boot toggle');
        check(byId('boot').textContent === 'Start sync on cold boot · On', 'Cold-boot-only label');
        byId('sync').click();
    },
    function () {
        check(byId('sync').textContent === 'Stop sync' && byId('reload').disabled, 'Streaming state');
        check(!byId('brightness').disabled, 'Live brightness slider enabled');
        byId('brightness').value = 42;
        byId('brightness').dispatchEvent(new Event('input'));
        byId('intensities').lastElementChild.click();
    },
    function () {
        check(state.brightness === 42 && state.intensity === 3, 'Live control save');
        document.dispatchEvent(new Event('visibilitychange'));
    },
    function () {
        check(byId('brightnessValue').textContent === '42%' && byId('room').textContent.indexOf('Intense') >= 0, 'Playback refresh');
        byId('sync').click();
    },
    function () {
        check(byId('state').textContent === 'Ready to sync', 'Returned to idle');
        state.wakeRetry = true;
        document.dispatchEvent(new Event('visibilitychange'));
    },
    function () {
        check(byId('state').textContent === 'Waiting to retry sync…', 'Waiting for TV input');
        check(byId('sync').textContent === 'Stop sync' && !byId('sync').disabled, 'Cancel wake retry');
        byId('sync').click();
    },
    function () {
        check(!state.wakeRetry && byId('state').textContent === 'Ready to sync', 'Wake retry cancelled');
        document.body.setAttribute('data-test-result','passed');
    }
];
function step() {
    try { steps.shift()(); if (steps.length) setTimeout(step, 100); }
    catch (err) { document.body.setAttribute('data-test-error', err.message); }
}
setTimeout(step, 100);
</script>
"""


def main():
    with tempfile.TemporaryDirectory(prefix="browser-", dir=ROOT / "build") as work:
        work = Path(work)
        app = Path(shutil.copytree(ROOT / "app", work / "app"))
        index = app / "index.html"
        html = index.read_text(encoding="utf-8").replace('<script src="screen.js">', FIXTURE + '<script src="screen.js">')
        index.write_text(html.replace("</body>", CHECK + "</body>"), encoding="utf-8")
        cmd = [sys.argv[1], "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
               "--disable-extensions", "--window-size=1920,900", "--dump-dom", "--virtual-time-budget=3000",
               "--user-data-dir=" + str(work / "profile"), index.as_uri()]
        result = subprocess.run(cmd, capture_output=True, timeout=40)
        html = result.stdout.decode("utf-8", errors="replace")
        if '<body data-test-result="passed">' not in html:
            raise ValueError(html[html.find("<body"):html.find("<main")] or result.stderr.decode(errors="replace")[:1000])
    print("TV setup passed pairing, area selection, remote focus, boot toggle, start and stop in Chromium.")


if __name__ == "__main__":
    main()
