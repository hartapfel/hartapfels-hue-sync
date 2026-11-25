"""Exercise region geometry, packed pixel loads, color normalization and frame acceptance."""
import ctypes as ct
import json
import math
from pathlib import Path
import random
import struct
import sys

from unicorn import UC_HOOK_CODE, UC_PROT_EXEC, UC_PROT_READ
from unicorn import x86_const as reg
from arm_bitmap import ArmBitmap
from capture_bitmap import Frame, Plane, Region
from desktop_bitmap import DesktopBitmap, COUNTS, IMAGE_BASE, RESULT, XY
from desktop_color import Triple, bits, scalar


class Screen(ct.Structure):
    _fields_ = [(key, ct.c_float) for key in ("left", "top", "right", "bottom")]


class State(ct.Structure):
    _fields_ = [(key, Triple) for key in ("trimean", "average", "lab")]


class Area(ct.Structure):
    _fields_ = [("start", Triple), ("end", Triple), ("screen", Screen)]


ZONE, PRESET, BP, SP, PIXELS = 0x23000, 0x24000, 0x25700, 0x26000, 0x30000


class DesktopVideo(DesktopBitmap):
    def __init__(self, exe):
        super().__init__(exe)
        vm = self.vm
        data = Path(exe).read_bytes()
        vm.mem_map(0x20000, 0x30000)
        for page in (0x160000, 0x170000, 0x390000, 0x2b000, 0x1bc000, 0x1ce000, 0x1cf000):
            vm.mem_map(IMAGE_BASE + page, 4096, UC_PROT_READ | UC_PROT_EXEC)
        vm.mem_map(IMAGE_BASE + 0x1310000, 4096, UC_PROT_READ)
        for start, end in [(0x1602f0, 0x1603c9), (0x161636, 0x16173d), (0x162980, 0x162aad),
                           (0x1626e9, 0x16277c), (0x170d70, 0x170e72),
                           (0x1cef20, 0x1cf028), (0x2b3a0, 0x2b3a5),
                           (0x1bcdc0, 0x1bcdd5)]:
            vm.mem_write(IMAGE_BASE + start, data[start - 0xc00:end - 0xc00])
        for rva in (0x3c2654, 0x1310df0):
            vm.mem_write(IMAGE_BASE + rva, data[rva - 0x1600:rva - 0x1600 + 4])

        def floorf(machine, address, size, context):
            value = math.floor(scalar(machine, reg.UC_X86_REG_XMM0))
            machine.reg_write(reg.UC_X86_REG_XMM0, bits(value))
            sp = machine.reg_read(reg.UC_X86_REG_RSP)
            ret, = struct.unpack("<Q", machine.mem_read(sp, 8))
            machine.reg_write(reg.UC_X86_REG_RSP, sp + 8)
            machine.reg_write(reg.UC_X86_REG_RIP, ret)

        vm.hook_add(UC_HOOK_CODE, floorf, begin=IMAGE_BASE + 0x390766, end=IMAGE_BASE + 0x390766)

    def registers(self, **values):
        for name, value in values.items():
            self.vm.reg_write(getattr(reg, "UC_X86_REG_" + name), value)

    def run(self, start, end, limit=150000):
        self.vm.emu_start(IMAGE_BASE + start, IMAGE_BASE + end, count=limit)
        if self.vm.reg_read(reg.UC_X86_REG_RIP) != IMAGE_BASE + end:
            raise RuntimeError(f"Desktop stage {start:x} did not finish")

    def geometry(self, width, height, screen, limit):
        vm = self.vm
        self.registers(R15=ZONE, R14=PRESET, RBP=BP, RSP=SP, R9=width, RDI=height)
        vm.mem_write(ZONE + 0x64, bytes(screen))
        vm.mem_write(PRESET + 0x68, struct.pack("<I", limit))
        vm.mem_write(BP - 0x58, struct.pack("<I", width * height))
        vm.mem_write(BP + 0x68, struct.pack("<II", 1, width))
        self.run(0x1602f0, 0x160355)
        w, h = [vm.reg_read(r) for r in (reg.UC_X86_REG_RBX, reg.UC_X86_REG_R10)]
        if min(w, h) < 3:
            return w, h, 0, 0
        self.run(0x160355, 0x1603c9)
        return w, h, vm.reg_read(reg.UC_X86_REG_RDI), vm.reg_read(reg.UC_X86_REG_R11)

    def capture(self, pixels, layout, stride, w, h, budget, offset):
        vm = self.vm
        vm.mem_write(PIXELS, pixels)
        vm.mem_write(COUNTS, bytes(0x3030))
        vm.mem_write(XY, struct.pack("<QQ", COUNTS, 0))
        self.registers(RBP=BP)
        vm.mem_write(BP + 0x77, struct.pack("<I", layout))
        self.run(0x1626e9, 0x16277c)
        vm.mem_write(COUNTS + 0x3010, bytes(vm.mem_read(BP - 0x19, 32)))
        self.registers(RCX=PRESET, RDX=XY, R8=PIXELS + offset, R9=stride, RSP=SP)
        vm.mem_write(SP + 0x1470, struct.pack("<QQQQQ", w, h, budget, BP - 0x19, 4))
        self.run(0x161636, 0x16173d, limit=1000000)
        self._call(0x161b00, (0, RESULT, XY))
        return struct.unpack("<I", vm.mem_read(COUNTS + 0x300c, 4))[0]

    def accept(self):
        self._call(0x162980, (0, ZONE, RESULT))
        self.registers(RBX=PRESET, RDI=0, R8=0, R9=ZONE, RBP=BP, XMM3=bits(255))
        self.vm.mem_write(PRESET + 12, bytes([2]))
        self.run(0x170d70, 0x170e72)
        return tuple(struct.unpack("<3f", self.vm.mem_read(ZONE + off, 12)) for off in (0, 0x28))

    def contains(self, room, point):
        # EDK CuboidArea virtual membership method, ordinary non-inverted cuboid.
        self.vm.mem_write(0x28000, struct.pack("<QQ3dQ3dB", 0, 0, *room.start.values(),
                                               0, *room.end.values(), 0))
        self.vm.mem_write(0x28100, struct.pack("<Q3d", 0, *point))
        return self._call(0x1cef20, (0x28000, 0x28100)) & 255


def main():
    desktop = DesktopVideo(sys.argv[1])
    arm = ArmBitmap(sys.argv[4]) if len(sys.argv) > 4 else None
    lib = ct.CDLL(sys.argv[2])
    lib.c9_region_grid.argtypes = [ct.c_uint, ct.c_uint, Screen, ct.c_uint, ct.POINTER(Region),
                                   ct.POINTER(ct.c_uint)]
    lib.c9_video_ingest.argtypes = [ct.POINTER(State), ct.POINTER(Frame), Screen, ct.c_uint,
                                    ct.POINTER(ct.c_uint)]
    rng = random.Random(5091)
    width, height = 67, 43
    stride = 4 * width + 8
    state = State()
    cases = held = 0
    screens = [Screen(0, 0, 1, 1), Screen(.125, .3, .44, .9), Screen(.8, .63, 1, 1),
               Screen(0, 0, .03, 1)]
    if len(sys.argv) > 3:
        profiles = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))["Core"]["CaptureOption"]
        preset, = [p for p in profiles if p["Name"] == "Video Default" and p["ScreenCount"] == 1]
        assert len(preset["Areas"]) == 14
        lib.c9_video_areas.restype = ct.POINTER(Area)
        lib.c9_video_area_mask.argtypes = [ct.c_double] * 3
        lib.c9_video_area_mask.restype = ct.c_uint32
        areas = lib.c9_video_areas()
        points = set()
        for i, reference in enumerate(preset["Areas"]):
            room, rect = reference["HueArea"], reference["ScreenPos"]
            expected = Area(Triple(*(room[key] for key in ("X0", "Y0", "Z0"))),
                            Triple(*(room[key] for key in ("X1", "Y1", "Z1"))),
                            Screen(*(rect[key] for key in ("X0", "Y0", "X1", "Y1"))))
            assert bytes(areas[i]) == bytes(expected), i
            screens.append(expected.screen)
            center = tuple((a + b) / 2 for a, b in zip(expected.start.values(), expected.end.values()))
            points.add(center)
            for corner in (expected.start.values(), expected.end.values()):
                points.add(corner)
                for axis, face in enumerate(corner):
                    for boundary in (math.nextafter(face, -math.inf), face, math.nextafter(face, math.inf)):
                        point = list(center)
                        point[axis] = boundary
                        points.add(tuple(point))
        print("All 14 desktop screen rectangles and 3D room bounds match the installed preset.")
        points.update(tuple(rng.uniform(-1.1, 1.1) for _ in range(3)) for _ in range(100))
        masks = []
        for point in sorted(points):
            mask = sum(desktop.contains(areas[i], point) << i for i in range(14))
            assert lib.c9_video_area_mask(*point) == mask, point
            if arm:
                assert arm.area_mask(point) == mask, point
            masks.append(mask)
        assert 0 in masks and any(m.bit_count() > 1 for m in masks)
        print(f"{len(masks)} room positions matched desktop EDK area membership, including shared faces.")
    for screen in screens:
        for limit in (1, 1000, 5000):
            w, h, budget, offset = desktop.geometry(width, height, screen, limit)
            region, assigned = Region(), ct.c_uint()
            assert lib.c9_region_grid(width, height, screen, limit, ct.byref(region), ct.byref(assigned))
            assert (region.width, region.height, assigned.value) == (w, h, budget)
            if min(w, h) >= 3:
                assert region.x + width * region.y == offset
            for layout in (0, 1, 2):
                peak = 1023 if layout == 2 else 255
                base = rng.randbytes(stride * height)
                for level in (None, 0, 1, 2, 3, 7, peak // 2, peak):
                    word = (level or 0) * (0x100401 if layout == 2 else 0x10101)
                    pixels = base if level is None else struct.pack("<I", word) * (stride * height // 4)
                    frame = Frame(width, height, layout, Plane(pixels, len(pixels), stride), Plane(), 0, 0)
                    if min(w, h) >= 3:
                        n = desktop.capture(pixels, layout, stride, w, h, budget, region.y * stride + 4 * region.x)
                    else:
                        desktop.vm.mem_write(RESULT, struct.pack("<10I", 0, 0, 0, 0, 8, 0, 0, 0, 0, 8))
                        n = 0
                    target = desktop.accept()
                    before = state.trimean.values()
                    samples = ct.c_uint()
                    assert lib.c9_video_ingest(ct.byref(state), ct.byref(frame), screen, limit, ct.byref(samples))
                    assert samples.value == n, (screen.left, w, h, budget, samples.value, n)
                    assert (state.trimean.values(), state.average.values()) == target, (cases, target, before)
                    if arm:
                        assert arm.ingest(frame, screen, limit, pixels) == (target, n), (cases, target)
                    held += before == target[0]
                    cases += 1
    print(f"{cases} packed-frame region targets matched desktop instructions, including {held} held targets.")
    if arm:
        print("The linked ARM processor matched the same sample counts, targets and area memberships.")

    # A stable mean hides a trimean change, and a stable trimean hides a mean
    # change. Exercise both cases from the same accepted uniform gray target.
    desktop.vm.mem_write(ZONE, bytes(0x64))
    state = State()
    screen = Screen(0, 0, 1, 1)
    w, h, budget, offset = desktop.geometry(width, height, screen, 1000)
    grid = list(desktop.grid(w, h, budget))
    for low, high, expected in [(60, 60, 60), (40, 140, 60), (60, 200, 60), (80, 180, 80)]:
        raw = bytearray(stride * height)
        for i, (x, y) in enumerate(grid):
            color = high if i % 5 == 0 else low
            pos = y * stride + 4 * x
            raw[pos:pos + 4] = bytes((color, color, color, 255))
        pixels = bytes(raw)
        frame = Frame(width, height, 0, Plane(pixels, len(pixels), stride), Plane(), 0, 0)
        desktop.capture(pixels, 0, stride, w, h, budget, offset)
        target = desktop.accept()
        samples = ct.c_uint()
        assert lib.c9_video_ingest(ct.byref(state), ct.byref(frame), screen, 1000, ct.byref(samples))
        assert state.trimean.values() == target[0] == (ct.c_float(expected / 255).value,) * 3
        assert state.average.values() == target[1]
    print("Mixed-brightness frames held or changed on the same frames as the desktop.")


if __name__ == "__main__":
    main()
