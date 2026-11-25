"""Compare the C port with the desktop grid and histogram routines on generated pixels.

Arguments: path to the installed HueSync.exe, path to the built c9_bitmap library.
The optional development dependency is unicorn==2.1.4. The emulator only receives
the two grid routines, histogram reduction, read-only constants, and test memory.
"""
import ctypes as ct
import hashlib
from pathlib import Path
import random
import struct
import sys

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn.x86_const import (
    UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,
    UC_X86_REG_RSP, UC_X86_REG_RIP, UC_X86_REG_RAX,
)

IMAGE_BASE = 0x140000000
STATE, XY, COUNTS, RESULT, STACK = 0x11000, 0x13000, 0x14000, 0x18000, 0x1eff8
STOP = 0x10000


class DesktopBitmap:
    def __init__(self, exe):
        data = Path(exe).read_bytes()
        if hashlib.sha256(data).hexdigest() != "2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad":
            raise ValueError("This comparison is specific to Hue Sync Desktop 1.13.1.83 (Windows x64).")
        self.vm = Uc(UC_ARCH_X86, UC_MODE_64)
        self.vm.mem_map(STOP, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.vm.mem_map(IMAGE_BASE + 0x14e000, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.vm.mem_map(IMAGE_BASE + 0x161000, 0x2000, UC_PROT_READ | UC_PROT_EXEC)
        for start, end in [(0x14e770, 0x14e940), (0x14ead0, 0x14eb76), (0x161b00, 0x162210)]:
            self.vm.mem_write(IMAGE_BASE + start, data[start - 0xc00:end - 0xc00])
        for page, addresses in [(0x3c2000, [0x3c2640]), (0x3c8000, [0x3c8830, 0x3c8894])]:
            self.vm.mem_map(IMAGE_BASE + page, 0x1000, UC_PROT_READ)
            for rva in addresses:
                self.vm.mem_write(IMAGE_BASE + rva, data[rva - 0x1600:rva - 0x1600 + 4])

    def _call(self, routine, args):
        self.vm.mem_write(STACK, struct.pack("<Q", STOP) + bytes(32))
        for reg, value in zip((UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9), args):
            self.vm.reg_write(reg, value)
        if len(args) == 5:
            self.vm.mem_write(STACK + 40, struct.pack("<Q", args[4]))
        self.vm.reg_write(UC_X86_REG_RSP, STACK)
        self.vm.emu_start(IMAGE_BASE + routine, STOP, count=150000)
        if self.vm.reg_read(UC_X86_REG_RIP) != STOP:
            raise RuntimeError("Desktop arithmetic did not return within its instruction budget")
        return self.vm.reg_read(UC_X86_REG_RAX)

    def grid(self, width, height, budget):
        self._call(0x14e770, (STATE, 0, width, height, budget))
        while self._call(0x14ead0, (STATE, XY, XY + 4, XY + 8)) & 255:
            yield struct.unpack("<II", self.vm.mem_read(XY, 8))

    def reduce(self, samples, bits):
        hist = [[0] * 1024 for _ in range(3)]
        sums = [0, 0, 0]
        for rgb in samples:
            for i, value in enumerate(rgb):
                hist[i][value] += 1
                sums[i] += value
        counts = [*hist[0], *hist[1], *hist[2], *sums, len(samples), *([0] * 6), 1 << bits, bits]
        self.vm.mem_write(COUNTS, struct.pack("<%dI" % len(counts), *counts))
        self.vm.mem_write(XY, struct.pack("<QQ", COUNTS, 0))
        self._call(0x161b00, (0, RESULT, XY))
        values = struct.unpack("<10I", self.vm.mem_read(RESULT, 40))
        return (*values[:3], *values[5:8], len(samples))


def main():
    original = DesktopBitmap(sys.argv[1])
    port = ct.CDLL(sys.argv[2]).analyze_bitmap
    port.argtypes = [ct.c_char_p, ct.c_size_t, ct.c_uint, ct.c_uint, ct.c_size_t, ct.c_int,
                     ct.c_uint, ct.POINTER(ct.c_uint32)]
    rng = random.Random(20261125)
    cases = 0
    for width, height, budget in [(1, 1, 1), (2, 1, 2), (4, 3, 12), (23, 16, 271),
                                  (32, 48, 350), (128, 72, 1000), (256, 144, 5000)]:
        points = list(original.grid(width, height, budget))
        for layout in range(3):
            bits = 10 if layout == 2 else 8
            stride = 4 * width + 12
            for levels in (2, 1 << bits):
                pixels = bytearray(stride * height)
                colors = []
                for y in range(height):
                    for x in range(width):
                        rgb = tuple(rng.randrange(levels) * ((1 << bits) - 1) // (levels - 1) for _ in range(3))
                        colors.append(rgb)
                        if bits == 10:
                            struct.pack_into("<I", pixels, y * stride + 4 * x, rgb[0] | rgb[1] << 10 | rgb[2] << 20)
                        else:
                            pixels[y * stride + 4 * x:y * stride + 4 * x + 3] = bytes(rgb if layout == 0 else rgb[::-1])
                expected = original.reduce([colors[y * width + x] for x, y in points], bits)
                result = (ct.c_uint32 * 7)()
                assert port(bytes(pixels), len(pixels), width, height, stride, layout, budget, result)
                assert tuple(result) == expected, (width, height, budget, layout, levels, tuple(result), expected)
                cases += 1
    print(f"{cases} desktop bitmap comparisons passed (grid, integer means and trimeans).")


if __name__ == "__main__":
    main()
