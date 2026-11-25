"""Send Hue v2 datagrams to an independent OpenSSL DTLS-PSK peer on localhost."""
import ctypes as ct
import math
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time


class Channel(ct.Structure):
    _fields_ = [("id", ct.c_uint8), ("r", ct.c_double), ("g", ct.c_double), ("b", ct.c_double)]


def main():
    lib = ct.CDLL(str(Path(sys.argv[1]).resolve()))
    openssl = sys.argv[2] if len(sys.argv) > 2 else "openssl"
    oracle = None
    if len(sys.argv) > 3:
        from desktop_serializer import DesktopSerializer
        oracle = DesktopSerializer(sys.argv[3])
    lib.hue_stream_open.argtypes = [ct.POINTER(ct.c_void_p), ct.c_char_p, ct.c_uint16,
                                    ct.c_char_p, ct.c_char_p, ct.c_char_p]
    lib.hue_stream_write.argtypes = [ct.c_void_p, ct.POINTER(Channel), ct.c_size_t]
    lib.hue_stream_close.argtypes = [ct.c_void_p]
    lib.hue_stream_close.restype = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    area = b"01234567-89ab-cdef-8123-456789abcdef"
    key = secrets.token_bytes(16)
    identity = b"c9-loopback-test"
    stream = ct.c_void_p()
    channels = (Channel * 2)(Channel(11, 0, 0.5, 1), Channel(4, 0, 0, 0))
    server = subprocess.Popen([openssl, "s_server", "-4", "-dtls1_2", "-quiet", "-nocert", "-naccept", "1",
                               "-cipher", "PSK-AES128-GCM-SHA256", "-psk", key.hex(),
                               "-psk_identity", identity.decode(), "-accept", f"127.0.0.1:{port}"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    observed = bytearray()
    reader = threading.Thread(target=lambda: observed.extend(server.stdout.read()), daemon=True)
    reader.start()
    try:
        assert lib.hue_stream_open(ct.byref(stream), b"127.0.0.1", port, b"bad", identity, key) < 0
        assert not stream.value
        time.sleep(.3)
        deadline = time.monotonic() + 10
        while True:
            ret = lib.hue_stream_open(ct.byref(stream), b"127.0.0.1", port, area, identity, key)
            if ret == 0:
                break
            if server.poll() is not None or time.monotonic() > deadline:
                if server.poll() is None:
                    server.kill()
                server.wait()
                details = server.stderr.read().decode(errors="replace")
                raise RuntimeError(f"DTLS handshake failed ({ret}): {details}")
            time.sleep(.05)
        expected = bytearray()
        channels[1].id = channels[0].id
        assert lib.hue_stream_write(stream, channels, 2) < 0
        channels[1].id = 4
        for i in range(258):
            edge = min(i * 257, 65535) / 65535
            channels[0].r = edge
            channels[1].r = math.nextafter(edge, -math.inf)
            channels[1].g = math.nextafter(edge, math.inf)
            channels[1].b = i / 257 + (0.5 / 65535)
            assert lib.hue_stream_write(stream, channels, 2) == 0
            expected.extend(b"HueStream" + bytes((2, 0, i % 256, 0, 0, 0, 0)) + area)
            for c in channels:
                values = c.r, c.g, c.b
                if oracle:
                    expected.extend(oracle.channel(c.id, values))
                else:
                    expected.extend(struct.pack(">BHHH", c.id, *(int(min(1, max(0, v)) * 65535) for v in values)))
            time.sleep(.002)
        lib.hue_stream_close(stream)
        stream = None
        server.wait(timeout=8)
        reader.join(timeout=2)
        assert server.returncode == 0, server.stderr.read().decode(errors="replace")
        assert bytes(observed) == expected, (len(observed), len(expected))
    finally:
        if stream:
            lib.hue_stream_close(stream)
        if server.poll() is None:
            server.kill()
        server.wait()
        reader.join(timeout=2)
        server.stdin.close()
        server.stdout.close()
        server.stderr.close()
    print("OpenSSL received 258 exact HueStream v2 frames over DTLS 1.2 PSK, including sequence wrap.")
    if oracle:
        print("All 516 channel records byte-matched the installed Hue Sync Desktop serializer.")


if __name__ == "__main__":
    main()
