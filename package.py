"""Stage the ARM worker and webOS service, build the IPK, and verify its payload."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent
BACKEND = "libdile_vt_backend.so"
BACKEND_SHA256 = "2bc6a61c497ad147c8ad12071adae5423cbe2c2921aa6671df68e56d77b937ef"


def archive(path):
    members = {}
    with path.open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise ValueError("Expected a webOS ar package")
        while header := stream.read(60):
            if len(header) != 60 or header[58:] != b"`\n":
                raise ValueError("Invalid package member")
            size = int(header[48:58])
            name = header[:16].decode().strip().rstrip("/")
            members[name] = stream.read(size)
            stream.read(size % 2)
    if members.get("debian-binary") != b"2.0\n":
        raise ValueError("Invalid package version")
    return members


def verify(package, app, service, appid, version):
    archives = archive(package)
    with tarfile.open(fileobj=io.BytesIO(archives["data.tar.gz"])) as data:
        files = {m.name.removeprefix("./"): m for m in data if m.isfile()}
        for source, dest in [(app, "applications/" + appid), (service, "services/" + appid + ".service")]:
            for entry in source.iterdir():
                if entry.name == "package.properties":
                    continue
                item = files["usr/palm/" + dest + "/" + entry.name]
                content = data.extractfile(item).read()
                if entry.suffix == ".json":
                    assert json.loads(content) == json.loads(entry.read_bytes()), entry.name
                else:
                    assert content == entry.read_bytes(), entry.name
                if entry.name in ("c9-sync", "libc9_hue_stream.so", BACKEND):
                    assert content[:6] == b"\x7fELF\x01\x01" and struct.unpack_from("<H", content, 18)[0] == 40
                if entry.name == "c9-sync":
                    assert item.mode & 0o111 == 0o111, oct(item.mode)
        manifest = files["usr/palm/packages/" + appid + "/packageinfo.json"]
        info = json.load(data.extractfile(manifest))
        assert info["id"] == appid and info["version"] == version
        assert appid in info["app"] and appid + ".service" in info["services"]
        assert all("HueSync.exe" not in name and "settings.json" not in name for name in files)
    with tarfile.open(fileobj=io.BytesIO(archives["control.tar.gz"])) as control:
        item = next(m for m in control if m.name.removeprefix("./") == "control")
        fields = dict(line.split(": ", 1) for line in control.extractfile(item).read().decode().splitlines() if ": " in line)
        assert fields["Package"] == appid and fields["Version"] == version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ares-js", type=Path, required=True, help="Official ares-package.js CLI entry point")
    parser.add_argument("--arm", type=Path, default=ROOT / "build/arm")
    parser.add_argument("--mbedtls", type=Path, default=ROOT / "build/mbedtls-3.6.7")
    parser.add_argument("--zig-lib", type=Path, default=ROOT / "build/zig-toolchain/ziglang/lib")
    parser.add_argument("--flatbuffers", type=Path, default=ROOT / "build/flatbuffers-595bf0007ab1929570c7671f091313c8fc20644e")
    parser.add_argument("--piccap-ipk", type=Path, default=ROOT / "build/piccap-0.5.3.ipk")
    parser.add_argument("--output", type=Path, default=ROOT / "build/ipk")
    args = parser.parse_args()
    info = json.loads((ROOT / "app/appinfo.json").read_bytes())
    appid, version = info["id"], info["version"]
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-", dir=ROOT / "build") as work:
        work = Path(work)
        app = Path(shutil.copytree(ROOT / "app", work / "app"))
        service = Path(shutil.copytree(ROOT / "service", work / "service"))
        for name in ("c9-sync", "libc9_hue_stream.so"):
            shutil.copyfile(args.arm / name, service / name)
        with tarfile.open(fileobj=io.BytesIO(archive(args.piccap_ipk)["data.tar.gz"])) as data:
            member = "usr/palm/services/org.webosbrew.piccap.service/" + BACKEND
            backend = data.extractfile(member).read()
            if hashlib.sha256(backend).hexdigest() != BACKEND_SHA256:
                raise ValueError("Expected the DILE backend from the PicCap 0.5.3 release")
            (service / BACKEND).write_bytes(backend)
        shutil.copyfile(args.mbedtls / "LICENSE", service / "LICENSE-mbedTLS.txt")
        shutil.copyfile(args.flatbuffers / "LICENSE", service / "LICENSE-FlatBuffers.txt")
        for name in ("libcxx", "libcxxabi", "libunwind"):
            shutil.copyfile(args.zig_lib / name / "LICENSE.TXT", service / ("LICENSE-" + name + ".txt"))
        subprocess.run(["node", str(args.ares_js.resolve()), str(app), str(service),
                        "--no-minify", "--outdir", str(work)], check=True)
        package, = work.glob("*.ipk")
        verify(package, app, service, appid, version)
        dest = args.output / package.name
        shutil.copyfile(package, dest)
        print(f"Verified {dest}\nSHA-256 {hashlib.sha256(dest.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
