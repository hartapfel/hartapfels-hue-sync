"""Compare both color transforms and the per-channel filter to desktop CPU instructions."""
import ctypes as ct
from itertools import product
from pathlib import Path
import random
import struct
import sys

from unicorn import UC_HOOK_CODE, UC_PROT_EXEC, UC_PROT_READ
from unicorn import x86_const as x86
from desktop_bitmap import DesktopBitmap, IMAGE_BASE, STACK, STATE


class Triple(ct.Structure):
    _fields_ = [(c, ct.c_float) for c in "xyz"]

    def values(self):
        return self.x, self.y, self.z


def bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def scalar(vm, reg):
    return struct.unpack("<f", struct.pack("<I", vm.reg_read(reg) & 0xffffffff))[0]


def main():
    vm = DesktopBitmap(sys.argv[1]).vm
    data = Path(sys.argv[1]).read_bytes()
    lib = ct.CDLL(sys.argv[2])
    lib.c9_rgb_to_lab.argtypes = [Triple, ct.c_int]
    lib.c9_lab_to_rgb.argtypes = [Triple]
    lib.c9_filter_lab.argtypes = [Triple, Triple, ct.c_float, ct.c_float]
    for fn in (lib.c9_rgb_to_lab, lib.c9_lab_to_rgb, lib.c9_filter_lab):
        fn.restype = Triple
    lib.c9_temporal_alpha.argtypes = [ct.c_float, ct.c_float]
    lib.c9_temporal_alpha.restype = ct.c_float
    powf = ct.CDLL("ucrtbase").powf
    powf.argtypes = [ct.c_float, ct.c_float]
    powf.restype = ct.c_float
    regs = [getattr(x86, "UC_X86_REG_XMM%d" % i) for i in range(16)]
    # Only the numeric portion of the video interpolation loop is mapped.
    vm.mem_map(IMAGE_BASE + 0x171000, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
    vm.mem_write(IMAGE_BASE + 0x171843, data[0x171843 - 0xc00:0x171ac9 - 0xc00])
    vm.mem_write(IMAGE_BASE + 0x171ccc, data[0x171ccc - 0xc00:0x171f91 - 0xc00])
    vm.mem_map(IMAGE_BASE + 0x3ca000, 0x1000, UC_PROT_READ)
    vm.mem_map(IMAGE_BASE + 0x3c4000, 0x1000, UC_PROT_READ)
    for page in [0x3c2000, 0x3c4000, 0x3c8000, 0x3ca000]:
        vm.mem_write(IMAGE_BASE + page, data[page - 0x1600:page - 0x1600 + 0x1000])
    vm.mem_map(IMAGE_BASE + 0x390000, 0x1000, UC_PROT_READ | UC_PROT_EXEC)

    def power(machine, _address, _size, _unused):
        machine.reg_write(regs[0], bits(powf(scalar(machine, regs[0]), scalar(machine, regs[1]))))
        sp = machine.reg_read(x86.UC_X86_REG_RSP)
        ret, = struct.unpack("<Q", machine.mem_read(sp, 8))
        machine.reg_write(x86.UC_X86_REG_RSP, sp + 8)
        machine.reg_write(x86.UC_X86_REG_RIP, ret)

    vm.hook_add(UC_HOOK_CODE, power, begin=IMAGE_BASE + 0x39075a, end=IMAGE_BASE + 0x39075a)
    vm.reg_write(x86.UC_X86_REG_RSP, STACK)
    vm.reg_write(x86.UC_X86_REG_R14, STATE)
    vm.reg_write(x86.UC_X86_REG_R15, STATE + 512)
    vm.reg_write(regs[10], bits(1))
    vm.reg_write(regs[12], bits(0))
    points = list(product((0, 0.01, 0.18, 0.5, 1), repeat=3))
    rng = random.Random(42)
    points.extend(tuple(rng.random() for _ in range(3)) for _ in range(250))
    previous = Triple(10, -15, 33)
    alpha_l = lib.c9_temporal_alpha(.025, 60)
    alpha_ab = lib.c9_temporal_alpha(.30, 60)
    worst = [0., 0., 0.]
    for encoded in (0, 1):
        for point in points:
            for reg, value in zip((14, 13, 11, 9), (*point, .055)):
                vm.reg_write(regs[reg], bits(value))
            vm.reg_write(x86.UC_X86_REG_RDI, encoded)
            vm.emu_start(IMAGE_BASE + 0x171843, IMAGE_BASE + 0x171ac9, count=1200)
            assert vm.reg_read(x86.UC_X86_REG_RIP) == IMAGE_BASE + 0x171ac9
            expected_lab = [scalar(vm, regs[i]) for i in [7, 9, 8]]
            lab = lib.c9_rgb_to_lab(Triple(*point), encoded)
            vm.mem_write(STATE + 0x50, bytes(previous))
            vm.reg_write(regs[0], bits(alpha_l))
            vm.reg_write(regs[15], bits(alpha_ab))
            vm.emu_start(IMAGE_BASE + 0x171ccc, IMAGE_BASE + 0x171f91, count=1200)
            assert vm.reg_read(x86.UC_X86_REG_RIP) == IMAGE_BASE + 0x171f91
            expected_filter = struct.unpack("<3f", vm.mem_read(STATE + 0x50, 12))
            filtered = lib.c9_filter_lab(previous, lab, alpha_l, alpha_ab)
            expected_rgb = [scalar(vm, regs[i]) for i in [9, 8, 7]]
            rgb = lib.c9_lab_to_rgb(filtered)
            for stage, (reference, actual, tolerance) in enumerate([
                (expected_lab, lab.values(), 0.0002), (expected_filter, filtered.values(), 0.0002),
                (expected_rgb, rgb.values(), 0.00001),
            ]):
                error = max(abs(a-b) for a, b in zip(reference, actual))
                assert error <= tolerance, (stage, point, encoded, reference, actual)
                worst[stage] = max(worst[stage], error)
            previous = filtered
    print(f"{len(points)*2} RGB/Lab/filter/RGB comparisons passed; max absolute errors {worst}")


if __name__ == "__main__":
    main()
