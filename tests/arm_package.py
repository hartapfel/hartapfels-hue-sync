"""Boot an isolated ARM guest and exercise the binaries taken from a webOS IPK."""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile

from hdr_chart import PATCHES, yuv10
from worker_loopback import AREA, IDENTITY, ROOT, FIRST_SPLIT_RECORDS
sys.path.insert(0, str(ROOT))
from package import BACKEND, archive


class Initramfs:
    def __init__(self):
        self.files = {}

    def add(self, name, data=b"", mode=stat.S_IFREG | 0o644):
        self.files[name] = (data, mode)

    def import_tar(self, source, wanted=None):
        with tarfile.open(fileobj=source, mode="r:*") as tar:
            for item in tar:
                name = item.name.removeprefix("./").rstrip("/")
                if name in ("", ".") or (wanted and name not in wanted):
                    continue
                if item.isfile() or item.islnk():
                    self.add(name, tar.extractfile(item).read(), stat.S_IFREG | item.mode)
                elif item.issym():
                    self.add(name, item.linkname.encode(), stat.S_IFLNK | 0o777)
                elif item.isdir():
                    self.add(name, mode=stat.S_IFDIR | item.mode)

    def write(self, dest):
        # A newc archive preserves guest paths and permissions on a Windows host.
        output = bytearray()
        entries = dict(self.files)
        entries["dev/console"] = (b"", stat.S_IFCHR | 0o600)
        entries["TRAILER!!!"] = (b"", 0)
        for ino, (name, (data, mode)) in enumerate(entries.items(), 1):
            label = name.encode() + b"\0"
            major, minor = (5, 1) if name == "dev/console" else (0, 0)
            fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(label), 0)
            output += b"070701" + b"".join(f"{v:08x}".encode() for v in fields) + label
            output.extend(bytes(-len(output) % 4))
            output += data
            output.extend(bytes(-len(output) % 4))
        dest.write_bytes(gzip.compress(output, compresslevel=1, mtime=0))


def add_runtime(vm, assets, runtime):
    with (assets / "alpine.tar.gz").open("rb") as f:
        vm.import_tar(f)
    with (assets / "openssl.apk").open("rb") as f:
        vm.import_tar(f, {"usr/bin/openssl"})
    # Resolve the SDK symlinks in memory so the fixture also runs on Windows.
    vm.add("sdk", mode=stat.S_IFDIR | 0o755)
    sonames = {"ld-linux.so.3", "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1"}
    with tarfile.open(assets / "sdk-2026.tar.gz", mode="r|gz") as tar:
        targets, libraries = {}, {}
        for item in tar:
            if not item.name.rpartition("/")[0].endswith("/arm-webos-linux-gnueabi/sysroot/lib"):
                continue
            name = item.name.rsplit("/", 1)[1]
            if item.issym() and name in sonames:
                targets[name] = item.linkname
            elif item.isfile() and re.fullmatch(r"(ld|lib(c|m|dl|pthread|rt))-[0-9.]+\.so", name):
                libraries[name] = tar.extractfile(item).read()
            if targets.keys() == sonames and all(v in libraries for v in targets.values()):
                break
        for name in sorted(sonames):
            vm.add("sdk/" + name, libraries[targets[name]], stat.S_IFREG | 0o755)
    deb = archive(runtime)
    data, = [v for k, v in deb.items() if k.startswith("data.tar.")]
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for name in sorted(sonames):
            entry, = [m for m in tar if m.name.endswith("/arm-linux-gnueabi/" + name)]
            vm.add("lib/" + name, tar.extractfile(entry).read(), stat.S_IFREG | 0o755)


def add_fixture(vm, temp, args):
    vm.add("fixture", mode=stat.S_IFDIR | 0o755)
    vm.add("app", mode=stat.S_IFDIR | 0o755)
    vm.add("capture-fixture", mode=stat.S_IFDIR | 0o755)
    vm.add("capture-fixture/" + BACKEND, args.test_backend.read_bytes(), stat.S_IFREG | 0o755)
    with tarfile.open(fileobj=io.BytesIO(archive(args.ipk)["data.tar.gz"])) as tar:
        for filename in ("c9-sync", "libc9_hue_stream.so", "worker-session.js", BACKEND):
            item, = [m for m in tar if m.name.endswith(".service/" + filename)]
            content = tar.extractfile(item).read()
            if filename.endswith(".js"):
                (temp / filename).write_bytes(content)
            else:
                vm.add("app/" + filename, content, stat.S_IFREG | item.mode)
    config = [{"host": "127.0.0.1"}, {"applicationKey": IDENTITY, "clientKey": "00112233445566778899aabbccddeeff"},
              {"id": AREA, "channels": [{"id": 19, "x": -.8, "y": .8, "z": .8},
                                       {"id": 3, "x": .8, "y": .8, "z": .8}]},
              {"width": 96, "height": 64, "fps": 30, "matrix": 1, "range": 0, "source": "piccap"}, 2100]
    script = "process.stdout.write(require('./worker-session').apply(null, JSON.parse(process.argv[1])))"
    vm.add("fixture/descriptor", subprocess.check_output(["node", "-e", script, json.dumps(config)], cwd=temp))
    direct = list(config)
    direct[3] = dict(config[3], source="direct", width=320, height=180)
    vm.add("fixture/direct-session", bytes((100, 1)) +
           subprocess.check_output(["node", "-e", script, json.dumps(direct)], cwd=temp))
    if args.chart_reference:
        config[2]["channels"] = json.loads(args.chart_reference.read_text())["channels"]
        assert len(config[2]["channels"]) == 14
        vm.add("fixture/chart-descriptor", subprocess.check_output(["node", "-e", script, json.dumps(config)], cwd=temp))

    def packet(name, kind, fields):
        src = temp / (name + ".json")
        src.write_text(json.dumps({"command_type": kind, "command": fields}), encoding="utf-8")
        subprocess.run([args.flatc, "-b", "-o", temp, args.schema, src], check=True)
        wire = src.with_suffix(".bin").read_bytes()
        vm.add("fixture/" + name, struct.pack(">I", len(wire)) + wire)

    def image(name, kind, fields):
        packet(name, "Image", {"data_type": kind, "data": dict(width=96, height=64, **fields)})

    packet("register", "Register", {"origin": "ARM VM", "priority": 150})
    expected = [[round(c/4) for c in yuv10(p)] for p in PATCHES]
    y_plane, uv_plane, uv_422 = [], [], []
    for y in range(64):
        row = expected[(y//16)*6:(y//16+1)*6]
        y_plane.extend([p[0] for p in row for _ in range(16)] + [250]*6)
        chroma = [c for p in row for _ in range(8) for c in p[1:]] + [250]*6
        uv_422.extend(chroma)
        if y % 2 == 0:
            uv_plane.extend(chroma)
    vm.add("fixture/direct.nv16", bytes(y_plane + uv_422))
    image("chart", "NV12Image", {"stride_y": 102, "stride_uv": 102, "data_y": y_plane, "data_uv": uv_plane})
    image("split", "RawImage", {"data": ([255, 0, 0]*48 + [0, 0, 255]*48)*64})
    image("white", "NV12Image", {"data_y": [235]*6144, "data_uv": [128]*3072})
    vm.add("init", Path(__file__).with_name("arm_guest.sh").read_bytes().replace(b"\r\n", b"\n"),
           stat.S_IFREG | 0o755)
    return expected


def verify(log, expected):
    assert "C9-ARM-PASSED" in log, log[-3000:]
    reports = [json.loads(s) for s in log.splitlines() if s.startswith('{"chart"')]
    assert [(r["format"], r["frames"], r["patches"]) for r in reports] == [
        ("NV16", 8, expected), ("NV12", 8, expected)]
    block = re.search(r"C9-PACKETS-BEGIN\s+([A-Za-z0-9+/=\s]+?)\s+C9-PACKETS-END", log)
    assert block, "Guest did not report the decrypted stream"
    packets = base64.b64decode("".join(block[1].split()), validate=True)
    assert len(packets) % 66 == 0
    assert packets[52:66] == FIRST_SPLIT_RECORDS, packets[52:66].hex()
    frames = []
    for index in range(len(packets)//66):
        packet = packets[66*index:66*(index+1)]
        assert packet[:52] == b"HueStream" + bytes((2, 0, index % 256, 0, 0, 0, 0)) + AREA.encode()
        left, r, g, b, right, rr, gg, bb = struct.unpack(">BHHHBHHH", packet[52:])
        assert (left, right) == (19, 3)
        frames.append((r, g, b, rr, gg, bb))
    assert len(frames) >= 15 and any(r > b and bb > rr for r, g, b, rr, gg, bb in frames)
    assert min(frames[-1][1], frames[-1][4]) > 5000
    controls = re.search(r"C9-CONTROLS-BEGIN\s+([A-Za-z0-9+/=\s]+?)\s+C9-CONTROLS-END", log)
    assert controls, "Guest did not report the live control stream"
    stream = base64.b64decode("".join(controls[1].split()), validate=True)
    assert len(stream) % 66 == 0 and stream[52:66] == FIRST_SPLIT_RECORDS
    records = [struct.unpack(">BHHHBHHH", stream[i+52:i+66]) for i in range(0, len(stream), 66)]
    dark = [i for i, (a,r,g,b,z,rr,gg,bb) in enumerate(records) if max(r,g,b,rr,gg,bb) == 0]
    assert len(dark) >= 3 and dark == list(range(dark[0], dark[-1]+1)) and dark[0] > 0
    left,r,g,b,right,rr,gg,bb = records[-1]
    assert (left,right) == (19,3) and min(r,bb) > 64000, records[-1]
    assert max(g,b,rr,gg) < min(r,bb) // 4, records[-1]
    print(f"Live controls muted both channels for {len(dark)} packets and resumed the red/blue split at 50%.")
    print(f"All 24 direct NV16 fixture and PicCap NV12 patches matched. "
          f"The packaged worker delivered {len(frames)} decrypted two-channel frames.")


def verify_chart_stream(log, reference):
    assert reference.get("brightnessPercent") == 100, "Regenerate the chart reference at desktop maximum brightness"
    match = re.search(r"C9-CHART-STREAM\s+(\d+)\s+([A-Za-z0-9+/=\s]+?)\s+C9-CHART-END", log)
    assert match, "Missing chart stream"
    count, remainder = divmod(int(match[1]), 150)
    endpoints = base64.b64decode("".join(match[2].split()), validate=True)
    assert len(endpoints) == 300 and not remainder and count >= 30
    for packet, sequence, key, tolerance in [(endpoints[:150], 0, "first", 0),
                                             (endpoints[150:], count-1, "settled", 2)]:
        assert packet[:52] == b"HueStream" + bytes((2, 0, sequence % 256, 0, 0, 0, 0)) + AREA.encode()
        expected = bytes.fromhex(reference[key])
        assert len(expected) == 98
        worst = 0
        for actual, target in zip(struct.iter_unpack(">B3H", packet[52:]), struct.iter_unpack(">B3H", expected)):
            assert actual[0] == target[0]
            error = max(abs(a-b) for a, b in zip(actual[1:], target[1:]))
            assert error <= tolerance, (key, actual, target, error)
            worst = max(worst, error)
        print(f"14-channel chart {key} frame: maximum difference {worst} RGB16 codes from desktop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ipk", "assets", "qemu", "flatc", "schema", "runtime"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--test-backend", type=Path, default=ROOT / "build/arm/libc9-test-capture-backend.so")
    parser.add_argument("--desktop-vectors", type=Path, help="Tick vectors from tests/desktop_color.py --arm-vectors")
    parser.add_argument("--chart-reference", type=Path, help="Desktop channel output from tests/desktop_color.py --chart-reference")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as work:
        temp = Path(work)
        vm = Initramfs()
        add_runtime(vm, args.assets, args.runtime)
        expected = add_fixture(vm, temp, args)
        if args.desktop_vectors:
            vectors = args.desktop_vectors.read_bytes()
            if not vectors or len(vectors) % 40:
                raise ValueError("Expected complete 40-byte desktop tick records")
            vm.add("fixture/desktop-ticks", vectors)
        initrd = temp / "test.cpio.gz"
        vm.write(initrd)
        result = subprocess.run([args.qemu, "-machine", "virt", "-cpu", "cortex-a15", "-m", "256", "-smp", "2",
                                 "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                                 "-nic", "none", "-no-reboot", "-kernel", args.assets / "vmlinuz", "-initrd", initrd,
                                 "-append", "console=ttyAMA0 rdinit=/init panic=-1 loglevel=3"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        log = result.stdout.decode(errors="replace")
        verify(log, expected)
        if args.chart_reference:
            verify_chart_stream(log, json.loads(args.chart_reference.read_text()))
        if args.desktop_vectors:
            summary = next(line for line in log.splitlines() if line.startswith("Desktop ticks "))
            assert int(summary.split()[2]) == len(vectors)//40
            print(summary)


if __name__ == "__main__":
    main()
