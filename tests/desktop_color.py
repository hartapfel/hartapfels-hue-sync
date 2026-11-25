"""Compare both color transforms and the per-channel filter to desktop CPU instructions."""
import argparse
import ctypes as ct
from itertools import product
from pathlib import Path
import random
import struct

from unicorn import UC_HOOK_CODE, UC_PROT_EXEC, UC_PROT_READ
from unicorn import x86_const as x86
from desktop_bitmap import DesktopBitmap, IMAGE_BASE, STACK, STATE
from desktop_area import DesktopArea
from worker_loopback import FIRST_SPLIT_RECORDS


class Triple(ct.Structure):
    _fields_ = [(c, ct.c_float) for c in "xyz"]

    def values(self):
        return self.x, self.y, self.z


def bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def scalar(vm, reg):
    return struct.unpack("<f", struct.pack("<I", vm.reg_read(reg) & 0xffffffff))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("desktop", type=Path)
    parser.add_argument("library", type=Path)
    parser.add_argument("--arm-vectors", type=Path, help="Save desktop tick outputs for the ARM package fixture")
    parser.add_argument("--chart-reference", type=Path, nargs=2, metavar=("PRESET_CONFIG", "OUTPUT"),
                        help="Write the 14-channel desktop reference for the ARM NV12 chart stream")
    args = parser.parse_args()
    vm = DesktopBitmap(args.desktop).vm
    data = args.desktop.read_bytes()
    lib = ct.CDLL(str(args.library.resolve()))
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

    def math_call(machine, address, _size, _unused):
        function = powf if address == IMAGE_BASE + 0x39075a else min
        machine.reg_write(regs[0], bits(function(scalar(machine, regs[0]), scalar(machine, regs[1]))))
        sp = machine.reg_read(x86.UC_X86_REG_RSP)
        ret, = struct.unpack("<Q", machine.mem_read(sp, 8))
        machine.reg_write(x86.UC_X86_REG_RSP, sp + 8)
        machine.reg_write(x86.UC_X86_REG_RIP, ret)

    vm.hook_add(UC_HOOK_CODE, math_call, begin=IMAGE_BASE + 0x39075a, end=IMAGE_BASE + 0x39075a)
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
    # Run the complete ordinary per-zone output path through gamut mode 2 and
    # the final black cutoff, with the desktop's preset coefficients in memory.
    for page in [0x172000, 0xc4000, 0xc5000, 0x38f000]:
        vm.mem_map(IMAGE_BASE + page, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
    for start, end in [(0x1714fc, 0x172169), (0x1723e5, 0x172404),
                       (0xc4940, 0xc49b3), (0xc5031, 0xc504b),
                       (0x38f0f0, 0x38f1f9)]:
        vm.mem_write(IMAGE_BASE + start, data[start - 0xc00:end - 0xc00])
    vm.mem_map(IMAGE_BASE + 0x1406000, 0x1000, UC_PROT_READ)
    vm.mem_map(IMAGE_BASE + 0x3ba000, 0x1000, UC_PROT_READ)
    vm.mem_write(IMAGE_BASE + 0x3ba4c0, struct.pack("<Q", IMAGE_BASE + 0x390800))
    vm.hook_add(UC_HOOK_CODE, math_call, begin=IMAGE_BASE + 0x390800, end=IMAGE_BASE + 0x390800)
    lib.c9_video_moderate_tick.argtypes = [Triple, ct.POINTER(Triple), ct.c_float, ct.c_float, ct.c_uint]
    lib.c9_video_moderate_tick.restype = Triple
    frame, config, preset, areas = STATE, STATE + 0x400, STATE + 0x800, STATE + 0xc00
    sp, bp = 0x1d000, 0x1d150
    vm.mem_write(preset + 0x44, struct.pack("<ff", .30, .025))
    vm.mem_write(preset + 0x9c, struct.pack("<I", 2))
    vm.mem_write(config + 0x2b0, bytes([1]))
    vm.mem_write(areas + 0x20, struct.pack("<QQ", frame, frame + 0x88))
    vm.mem_write(sp + 0x70, struct.pack("<Q", areas))
    vm.mem_write(areas + 0x40, struct.pack("<Q", areas + 0x80))
    # The installed zero-ms SyncDelay makes the desktop allocate one RGB triple.
    ring, slot = areas + 0x100, areas + 0x180
    vm.mem_write(areas + 0x80, struct.pack("<Q", ring))
    vm.mem_write(ring + 0x10, struct.pack("<I", 1))
    vm.mem_write(ring + 0x20, struct.pack("<QQ", slot, slot + 24))

    def desktop_tick(point, fps):
        vm.mem_write(frame, struct.pack("<4fI", *point, 0, 1))
        for reg, value in [(x86.UC_X86_REG_RSP, sp), (x86.UC_X86_REG_RBP, bp),
                           (x86.UC_X86_REG_R15, preset), (x86.UC_X86_REG_R13, config),
                           (x86.UC_X86_REG_RDI, areas)]:
            vm.reg_write(reg, value)
        for reg, value in [(6,fps),(8,12.92),(9,.055),(10,1),(12,0)]:
            vm.reg_write(regs[reg], bits(value))
        vm.emu_start(IMAGE_BASE + 0x1714fc, IMAGE_BASE + 0x172169, count=1700)
        assert vm.reg_read(x86.UC_X86_REG_RIP) == IMAGE_BASE + 0x172169
        queued = tuple(struct.unpack("<d", vm.reg_read(regs[j]).to_bytes(16, "little")[:8])[0]
                       for j in (1, 2, 3))
        assert queued == struct.unpack("<3d", vm.mem_read(slot, 24))
        return queued

    # The same two targets reach the ARM worker over PicCap in arm_package.py.
    # Compute their first records using only desktop instructions.
    effect = DesktopArea(args.desktop)
    records = bytearray()
    vm.mem_write(config + 0xe8, struct.pack("<f", 1.))
    vm.mem_write(config + 0xf0, struct.pack("<d", 1./255))
    for identifier, target in ((19, (1,0,0)), (3, (0,0,1))):
        vm.mem_write(frame + 0x50, bytes(Triple()))
        effect.set_color(desktop_tick(target, 60.))
        layer = effect.render((0., 0., 0.))
        records.extend(effect.channel(identifier, effect.mix([layer])))
    assert records == FIRST_SPLIT_RECORDS, records.hex()
    print("The ARM package fixture's first two channel records matched the desktop renderer and serializer.")
    if args.chart_reference:
        from desktop_chart import write_reference
        def chart_ticks(target):
            vm.mem_write(frame + 0x50, bytes(Triple()))
            first = desktop_tick(target, 60.)
            # Advance the desktop's own elapsed-time filter by one minute
            # with a constant target to obtain its settled output.
            return first, desktop_tick(target, 1. / 60.)
        write_reference(args.desktop, *args.chart_reference, chart_ticks, effect)
    cases = 0
    vectors = bytearray()
    for gain in (0.25, 1, 1.5, 5):
        for nominal_fps in (24, 60, None):
            for threshold in (1, 18):
                history = Triple(0, 0, 0)
                vm.mem_write(frame + 0x50, bytes(history))
                vm.mem_write(config + 0xe8, struct.pack("<f", gain))
                vm.mem_write(config + 0xf0, struct.pack("<d", threshold / 255))
                points_for_run = [(0,0,0), (0.01,0.01,0.01), *points[:125:7], (1,1,1), (0,0,0)]
                for i, point in enumerate(points_for_run):
                    fps = nominal_fps or 60
                    if nominal_fps is None and i:
                        elapsed_ns = (16_666_667, 8_500_000, 31_400_000, 4_100_000, 89_000_000)[i % 5]
                        vm.reg_write(x86.UC_X86_REG_RAX, 20_000_000_000 + elapsed_ns)
                        vm.reg_write(x86.UC_X86_REG_RBX, 20_000_000_000)
                        for idx, value in [(13, 1e9), (14, 1.)]:
                            vm.reg_write(regs[idx], int.from_bytes(struct.pack("<d", value), "little"))
                        vm.emu_start(IMAGE_BASE + 0x1723e5, IMAGE_BASE + 0x172404, count=16)
                        fps = ct.c_float(1. / (elapsed_ns / 1e9)).value
                        assert scalar(vm, regs[6]) == fps
                    queued = desktop_tick(point, fps)
                    if args.arm_vectors:
                        vectors.extend(struct.pack("<5f2I3f", *point, gain, fps, threshold, int(i == 0), *queued))
                    result = lib.c9_video_moderate_tick(Triple(*point),ct.byref(history),gain,fps,threshold)
                    assert max(abs(a-b) for a,b in zip(result.values(), queued)) <= 1e-5, (point, result.values(), queued)
                    cases += 1
    print(f"{cases} Video/Moderate ticks matched through the zero-delay queue, including irregular clock intervals.")
    if args.arm_vectors:
        args.arm_vectors.write_bytes(vectors)


if __name__ == "__main__":
    main()
