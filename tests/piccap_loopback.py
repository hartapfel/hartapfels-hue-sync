"""Feed PicCap wire messages into the worker and decrypt the resulting Hue stream."""
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

from worker_loopback import AREA, ROOT, reference_ticks, run


def read_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("PicCap input closed before reply")
        buf.extend(chunk)
    return bytes(buf)


def test(worker, library, node, openssl, flatc, schema_dir, tmp):
    def message(name, obj):
        src = tmp / (name + ".json")
        src.write_text(json.dumps(obj), encoding="utf-8")
        subprocess.run([flatc, "-b", "-o", tmp, schema_dir / "hyperion_request.fbs", src], check=True)
        payload = src.with_suffix(".bin").read_bytes()
        return struct.pack(">I", len(payload)) + payload

    def image(fmt, data):
        return message(fmt, {"command_type": "Image", "command": {"data_type": fmt, "data": data}})

    w, h = 64, 48
    register = message("register", {"command_type": "Register", "command": {"origin": "webos", "priority": 150}})
    rgb = image("RawImage", {"width": w, "height": h, "data": ([255,0,0]*(w//2) + [0,0,255]*(w//2))*h})
    # Padding has conspicuously different values. Every real pixel should decode as white.
    nv12 = image("NV12Image", {"width": w, "height": h, "stride_y": w+7, "stride_uv": w+8,
                 "data_y": ([235]*w + [0]*7)*h, "data_uv": ([128]*w + [0]*8)*(h//2)})
    expected = reference_ticks(library)

    def feed():
        deadline = time.monotonic() + 6
        while True:
            try:
                sock = socket.create_connection(("127.0.0.1", 19401), timeout=.1)
                break
            except OSError:
                if time.monotonic() >= deadline: raise
                time.sleep(.025)
        with sock:
            sock.settimeout(3)
            def send(packet):
                sock.sendall(packet)
                length, = struct.unpack(">I", read_exact(sock, 4))
                assert 0 < length < 1024
                return read_exact(sock, length)
            def reply(payload):
                f = tmp / "reply.bin"
                f.write_bytes(payload)
                subprocess.run([flatc, "-t", "--strict-json", "--raw-binary", "-o", tmp,
                                schema_dir / "hyperion_reply.fbs", "--", f], check=True)
                return json.loads(f.with_suffix(".json").read_text())
            # Split the header and body at boundaries unrelated to the schema.
            sock.sendall(register[:2])
            assert reply(send(register[2:]))["registered"] == 150
            ack = send(rgb)
            assert reply(ack).get("registered", -1) == -1
            for frame in [rgb]*15 + [nv12]*16:
                assert send(frame) == ack
                time.sleep(.03)
    packets = run(worker, node, openssl, False, feed)
    assert len(packets) % 66 == 0
    frames = []
    for i in range(len(packets)//66):
        packet = packets[i*66:(i+1)*66]
        assert packet[:52] == b"HueStream" + bytes((2, 0, i, 0, 0, 0, 0)) + AREA.encode()
        left, r,g,b, right, rr,gg,bb = struct.unpack_from(">BHHHBHHH",packet,52)
        assert (left,right) == (19,3)
        frames.append((r,g,b,rr,gg,bb))
    assert len(frames) >= 25 and all(frame in expected for frame in frames[:12])
    assert min(frames[-1][1], frames[-1][4]) > max(frames[10][1], frames[10][4]) + 5000
    print(f"Received PicCap registration, RGB24 and padded NV12; OpenSSL decrypted {len(frames)} corresponding Hue frames.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as work:
        test(*sys.argv[1:6], Path(sys.argv[6]).resolve(), Path(work))
