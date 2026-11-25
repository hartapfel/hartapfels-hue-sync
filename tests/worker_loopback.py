"""Exercise the service descriptor, frame processing, and encrypted worker output together."""
import ctypes as ct
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
AREA = "01234567-89ab-cdef-8123-456789abcdef"
IDENTITY = "c9-worker-test"


class Color(ct.Structure):
    _fields_ = [(c, ct.c_float) for c in "rgb"]


def reference_ticks(library, timestamps):
    tick = ct.CDLL(str(Path(library).resolve())).c9_video_moderate_tick
    tick.argtypes = [Color, ct.POINTER(Color), ct.c_float, ct.c_float, ct.c_uint]
    tick.restype = Color
    history = [Color(), Color()]
    result = []
    for i, timestamp in enumerate(timestamps):
        fps = 60. if i == 0 else 1. / ((timestamp - timestamps[i-1]) / 1e9)
        codes = []
        for target, old in zip([Color(1, 0, 0), Color(0, 0, 1)], history):
            color = tick(target, ct.byref(old), 1, fps, 1)
            codes.extend(int(max(0, min(1, v)) * 65535) for v in (color.r, color.g, color.b))
        result.append(tuple(codes))
    return result


def run(worker, node, openssl, failing, producer=None, slow_tick=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_hex(16)
    args = [{"host": "127.0.0.1"}, {"applicationKey": IDENTITY, "clientKey": key},
            {"id": AREA, "channels": [{"id": 19, "x": -.8, "y": .8, "z": .8},
                                     {"id": 3, "x": .8, "y": .8, "z": .8}]},
            {"width": 128, "height": 72, "fps": 10, "matrix": 1, "range": 0}, port]
    if producer:
        args[3]["source"] = "piccap"
    js = ("var s='';process.stdin.on('data',function(d){s+=d;});process.stdin.on('end',function(){"
          "process.stdout.write(require('./service/worker-session').apply(null,JSON.parse(s)));});")
    descriptor = subprocess.run([node, "-e", js], cwd=ROOT, input=json.dumps(args).encode(),
                                capture_output=True, check=True).stdout
    server = subprocess.Popen([openssl, "s_server", "-4", "-dtls1_2", "-quiet", "-nocert", "-naccept", "1",
                               "-cipher", "PSK-AES128-GCM-SHA256", "-psk", key, "-psk_identity", IDENTITY,
                               "-accept", f"127.0.0.1:{port}"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    env = dict(os.environ)
    if failing:
        env["C9_TEST_CAPTURE_FAIL"] = "1"
    if slow_tick:
        env["C9_TEST_SLOW_TICK"] = "1"
    try:
        time.sleep(.4)
        with subprocess.Popen([worker], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env) as proc, ThreadPoolExecutor(2) as pool:
            receiver = pool.submit(server.stdout.read)
            feed = pool.submit(producer) if producer else None
            try:
                stdout, stderr = proc.communicate(descriptor, timeout=20)
                if feed:
                    feed.result()
                # Keep stdin open until the DTLS close, draining stdout during streaming.
                server.wait(timeout=8)
            finally:
                if proc.poll() is None:
                    proc.kill()
                if server.poll() is None:
                    server.kill()
            packets = receiver.result()
        errors = server.stderr.read()
        assert server.returncode == 0, errors
        if failing:
            assert proc.returncode == 1 and not stdout and not packets and b"capture failed" in stderr, stderr
        elif producer:
            assert proc.returncode == 1 and stdout.splitlines() == [b"streaming"] and b"PicCap input disconnected" in stderr, stderr
        else:
            assert proc.returncode == 0 and stdout.splitlines() == [b"streaming"], (stdout, stderr)
        timestamps = [int(line[8:]) for line in stderr.splitlines() if line.startswith(b"tick-ns ")]
        assert all(a < b for a, b in zip(timestamps, timestamps[1:]))
        return packets, timestamps
    finally:
        if server.poll() is None:
            server.kill()
        server.communicate()


def main():
    worker, library, node, openssl = sys.argv[1:]
    malformed = subprocess.run([worker], input=b"C9S1", capture_output=True, timeout=5)
    assert malformed.returncode == 2 and not malformed.stdout
    packets, timestamps = run(worker, node, openssl, False, slow_tick=True)
    assert timestamps[12] - timestamps[11] >= 80_000_000
    ticks = reference_ticks(library, timestamps)
    size = 66
    assert len(packets) % size == 0
    count = len(packets) // size
    assert 30 <= count <= 90, count
    previous, distinct = 0, set()
    for seq in range(count):
        packet = packets[seq * size:(seq + 1) * size]
        assert packet[:52] == b"HueStream" + bytes((2, 0, seq, 0, 0, 0, 0)) + AREA.encode()
        left, r, g, b, right, rr, gg, bb = struct.unpack_from(">BHHHBHHH", packet, 52)
        assert (left, right) == (19, 3)
        sample = (r, g, b, rr, gg, bb)
        previous = ticks.index(sample, previous)
        distinct.add(sample)
    assert len(distinct) > 12 and r > b and bb > rr
    run(worker, node, openssl, True)
    print(f"{count} decrypted frames matched timed Video/Moderate ticks on both channels, including an 85 ms stall.")
    print("Malformed input and failed capture both exited without reporting an active stream.")


if __name__ == "__main__":
    main()
