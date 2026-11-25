"""Compare the desktop video mixer and RGB serializer for overlapping room volumes."""
import ctypes as ct
import random
import struct
import sys
from pathlib import Path

from desktop_serializer import DesktopSerializer
from stream_loopback import Channel
from arm_bitmap import ArmBitmap


class Layer(ct.Structure):
    _fields_ = [(key, ct.c_double) for key in ("r", "g", "b", "alpha")]


def main():
    desktop = DesktopSerializer(sys.argv[1])
    arm = ArmBitmap(sys.argv[3]) if len(sys.argv) > 3 else None
    lib = ct.CDLL(str(Path(sys.argv[2]).resolve()))
    lib.c9_mix_video_channel.argtypes = [ct.c_uint8, ct.c_uint32, ct.POINTER(Layer)]
    lib.c9_mix_video_channel.restype = Channel
    rng = random.Random(725)
    masks = [0, *(1 << i for i in range(14)), 0x3fff, *(rng.randrange(1 << 14) for _ in range(256))]
    for mask in masks:
        rgba = [(rng.random(), rng.random(), rng.random(), rng.choice([0., 1., rng.random()]))
                for _ in range(14)]
        channel = rng.randrange(256)
        layers = (Layer * 14)(*(Layer(*p) for p in rgba))
        out = lib.c9_mix_video_channel(channel, mask, layers)
        reference = desktop.mix(color for i, color in enumerate(rgba) if mask & 1 << i)
        assert out.id == channel and (out.r, out.g, out.b) == reference
        if arm:
            result, inputs = 0x1021000, 0x1022008
            arm.vm.mem_write(inputs, bytes(layers))
            arm.call("c9_mix_video_channel", [result, channel, mask, inputs])
            arm_out = Channel.from_buffer_copy(arm.vm.mem_read(result, ct.sizeof(Channel)))
            assert arm_out.id == channel and (arm_out.r, arm_out.g, arm_out.b) == reference
        assert struct.pack(">BHHH", out.id, *(int(v * 65535) for v in reference)) == desktop.channel(channel, reference)
    print(f"{len(masks)} channel mixes matched the desktop mixer in double precision and RGB16 serialization.")


if __name__ == "__main__":
    main()
