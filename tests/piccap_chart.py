"""Send a decoded HDR10 clip through the worker's capture-only PicCap entry point."""
import argparse
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from piccap_loopback import read_exact


def run(worker, ffmpeg, clip, flatc, schema, layout, tmp):
    w, h = 96, 64
    rgb = layout == "RGB24"
    fmt = "rgb24" if rgb else "nv12"
    data = subprocess.run([ffmpeg, "-loglevel", "error", "-i", clip, "-vf", f"scale={w}:{h}:flags=neighbor",
                           "-pix_fmt", fmt, "-frames:v", "1", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout
    assert len(data) == (w*h*3 if rgb else w*h*3//2)

    def packet(kind, fields):
        source = tmp / "input.json"
        source.write_text(json.dumps({"command_type": kind, "command": fields}), encoding="utf-8")
        subprocess.run([flatc, "-b", "-o", tmp, schema, source], check=True)
        wire = source.with_suffix(".bin").read_bytes()
        return struct.pack(">I", len(wire)) + wire

    def image(buf):
        body = {"width": w, "height": h}
        if rgb:
            body["data"] = list(buf)
        else:
            for plane, height, offset in (("y", h, 0), ("uv", h//2, w*h)):
                body["stride_" + plane] = w+6
                body["data_" + plane] = list(b"".join(buf[offset+r*w:offset+(r+1)*w] + bytes([250])*6
                                                      for r in range(height)))
        return packet("Image", {"data_type": "RawImage" if rgb else "NV12Image", "data": body})

    register = packet("Register", {"origin": "test", "priority": 150})
    chart, blank = image(data), image(bytes(len(data)))
    expected = []
    for i in range(24):
        x, y = (2*(i%6)+1)*w//12, (2*(i//6)+1)*h//8
        if rgb:
            expected.append(list(data[3*(y*w+x):3*(y*w+x)+3]))
        else:
            uv = w*h + y//2*w + x//2*2
            expected.append([data[y*w+x], *data[uv:uv+2]])
    with subprocess.Popen([worker, "--measure-hdr-chart"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    peer = socket.create_connection(("127.0.0.1", 19401), timeout=.5)
                    break
                except OSError:
                    if time.monotonic() >= deadline or proc.poll() is not None: raise
                    time.sleep(.05)
            with peer:
                peer.settimeout(3)
                for msg in [register, blank, *([chart]*7), blank, *([chart]*7)]:
                    peer.sendall(msg)
                    n, = struct.unpack(">I", read_exact(peer, 4))
                    assert 0 < n < 1024
                    read_exact(peer, n)
                peer.sendall(chart)
                out, err = proc.communicate(timeout=5)
        finally:
            if proc.poll() is None: proc.kill()
        assert proc.returncode == 0, err
        actual = json.loads(out)
        assert actual == {"chart": "c9-hdr10-v1", "width": w, "height": h, "format": layout,
                          "frames": 8, "patches": expected}, actual
        print(f"{layout}: all 24 captured patch triples matched the decoded clip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("worker", "ffmpeg", "clip", "flatc", "schema"):
        parser.add_argument(option, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        for layout in ("RGB24", "NV12"):
            run(args.worker, args.ffmpeg, args.clip, args.flatc, args.schema, layout, Path(temp))
