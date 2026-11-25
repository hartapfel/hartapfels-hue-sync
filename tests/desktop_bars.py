"""Compare frame-by-frame bar probes and crop rectangles to Hue Sync Desktop 1.13.1.83."""
import ctypes as ct
import math
from pathlib import Path
import struct
import sys

from unicorn import UC_HOOK_CODE, UC_PROT_EXEC, UC_PROT_READ
from unicorn import x86_const as x
from capture_bitmap import Frame, Plane, Region, decode
from desktop_bitmap import DesktopBitmap, IMAGE_BASE
from desktop_color import bits, scalar
from desktop_video import DesktopVideo, Screen, State as Zone, ZONE
from arm_bitmap import ArmBitmap

STATE, SP, OUT, PIXELS = 0x21000, 0x28000, 0x2a000, 0x31000


class Detector(DesktopBitmap):
    def __init__(self, exe):
        super().__init__(exe)
        data = Path(exe).read_bytes()
        vm = self.vm
        vm.mem_map(0x20000, 0x20000)
        for page, size in [(0x167000, 8192), (0x390000, 4096)]:
            vm.mem_map(IMAGE_BASE + page, size, UC_PROT_READ | UC_PROT_EXEC)
        for lo, hi in [(0x14e6e0, 0x14e76e), (0x14e940, 0x14eacc), (0x167250, 0x167940),
                       (0x167c40, 0x167d35), (0x167e32, 0x168209), (0x168399, 0x1683d9)]:
            vm.mem_write(IMAGE_BASE + lo, data[lo-0xc00:hi-0xc00])
        vm.mem_map(IMAGE_BASE + 0x3ca000, 0x3000, UC_PROT_READ)
        for rva in [0x3cacc8, 0x3cca10]:
            vm.mem_write(IMAGE_BASE + rva, data[rva-0x1600:rva-0x1600+4])

        def runtime(machine, addr, size, context):
            if addr in (IMAGE_BASE + 0x390700, IMAGE_BASE + 0x390706):
                dst, src, n = [machine.reg_read(r) for r in (x.UC_X86_REG_RCX, x.UC_X86_REG_RDX, x.UC_X86_REG_R8)]
                if n:
                    machine.mem_write(dst, bytes(machine.mem_read(src, n)))
                machine.reg_write(x.UC_X86_REG_RAX, dst)
            else:
                v = scalar(machine, x.UC_X86_REG_XMM0)
                v = v ** scalar(machine, x.UC_X86_REG_XMM1) if addr == IMAGE_BASE + 0x39075a else math.floor(v + .5)
                machine.reg_write(x.UC_X86_REG_XMM0, bits(v))
            sp = machine.reg_read(x.UC_X86_REG_RSP)
            ret, = struct.unpack("<Q", machine.mem_read(sp, 8))
            machine.reg_write(x.UC_X86_REG_RSP, sp + 8)
            machine.reg_write(x.UC_X86_REG_RIP, ret)

        for rva in (0x390700, 0x390706, 0x39075a, 0x390760):
            vm.hook_add(UC_HOOK_CODE, runtime, begin=IMAGE_BASE+rva, end=IMAGE_BASE+rva)
        vm.mem_write(STATE+8, struct.pack("<II5f", 201, 9, .4, .6, .4, .6, .23))
        vm.mem_write(STATE+0x13a8, b'\xff'*4)
        vm.reg_write(x.UC_X86_REG_RDI, STATE)
        vm.reg_write(x.UC_X86_REG_RBX, 1)
        vm.emu_start(IMAGE_BASE+0x168399, IMAGE_BASE+0x1683d9, count=12000)
        for i in range(4):
            buf = 0x2b000 + i * 0x400
            vm.mem_write(STATE+0x13b0+24*i, struct.pack("<3Q", buf, buf+201*4, buf+201*4))

    def detect(self, pixels, w, h, layout):
        vm = self.vm
        vm.mem_write(PIXELS, pixels)
        vm.mem_write(SP+0x50, struct.pack("<Q", PIXELS))
        vm.mem_write(SP+0x68, struct.pack("<Q", OUT))
        vm.mem_write(SP+0xa0, struct.pack("<5I", w*4, 4, w, h, layout))
        for reg, val in [(x.UC_X86_REG_RSP, SP), (x.UC_X86_REG_RSI, STATE), (x.UC_X86_REG_RBX, OUT)]:
            vm.reg_write(reg, val)
        vm.emu_start(IMAGE_BASE+0x167e32, IMAGE_BASE+0x168209, count=150000)
        assert vm.reg_read(x.UC_X86_REG_RIP) == IMAGE_BASE+0x168209
        left, top, right, bottom = struct.unpack("<4I", vm.mem_read(OUT, 16))
        index, = struct.unpack("<I", vm.mem_read(STATE+0x1410, 4))
        last = (index-1) % 201
        top_probe, = struct.unpack("<I", vm.mem_read(0x2b000+4*last, 4))
        left_probe, = struct.unpack("<I", vm.mem_read(0x2b400+4*last, 4))
        return (left, top, right-left, bottom-top), (left_probe, top_probe)


class History(ct.Structure):
    _fields_ = [('observed', ct.c_uint16 * 201), ('sorted', ct.c_uint16 * 201)]


class State(ct.Structure):
    _fields_ = [('random', ct.c_uint32 * 624), ('index', ct.c_uint), ('pos', ct.c_uint),
                ('horizontal', History), ('vertical', History)]


def images():
    w, h = 65, 43
    for i in range(530):
        layout = i % 6
        # Stable bars, then dark/bright transitions, asymmetric and irregular
        # boundaries reveal the precise random row/column sampled each frame.
        def color(x, y):
            if i < 110: return 0 if x < 5 or y < 3 or x >= w-5 or y >= h-3 else 90
            if i < 214: return 0
            if i < 340: return 3 if y < (x+i) % 8 or x < (y+i) % 12 else 4
            if i < 449: return 0 if x < 16 else 200
            return 0 if y < 6 else 25
        vals = [[color(x, y) for x in range(w)] for y in range(h)]
        uv = b''
        if layout in (4, 5):
            raw = bytes(v+16 for row in vals for v in row)
            uvstride = (w + 1)//2*2
            uv = bytes([128]) * uvstride * ((h+1)//2 if layout == 4 else h)
            lut = {v:bytes((*decode(v+16, 128, 128, 1, False), 0)) for v in set(rawvalue for row in vals for rawvalue in row)}
            packed = b''.join(lut[v] for row in vals for v in row)
            frame = Frame(w,h,layout,Plane(raw,len(raw),w),Plane(uv,len(uv),uvstride),1,0)
        else:
            def word(v):
                if layout == 2:
                    return struct.pack("<I", (4*v)*0x100401)
                return bytes([v,v,v,0])
            packed = b''.join(word(v) for row in vals for v in row)
            raw = packed if layout != 3 else b''.join(bytes([v,v,v]) for row in vals for v in row)
            frame = Frame(w,h,layout,Plane(raw,len(raw),len(raw)//h),Plane(),1,0)
        yield frame, raw, uv, packed, layout if layout < 3 else 0


def cropped_sampling(exe, lib):
    reference = DesktopVideo(exe)
    w, h = 65, 43
    picture = Region(5, 3, 55, 37)
    stride, chroma_stride = 70, 72
    y = bytes(16 + (29*x + 7*row) % 220 for row in range(h) for x in range(stride))
    uv = bytes(32 + (17*x + 13*row) % 193 for row in range((h+1)//2) for x in range(chroma_stride))
    frame = Frame(w,h,4,Plane(y,len(y),stride),Plane(uv,len(uv),chroma_stride),1,0)
    packed = bytearray()
    for row in range(h):
        for x in range(w):
            ci = (row//2)*chroma_stride + 2*(x//2)
            packed.extend((*decode(y[row*stride+x],uv[ci],uv[ci+1],1,False), 0))
    lib.c9_video_ingest_picture.argtypes = [ct.POINTER(Zone),ct.POINTER(Frame),Region,Screen,
                                             ct.c_uint,ct.POINTER(ct.c_uint)]
    for screen in [Screen(0,0,1,1),Screen(0,0,.3,.375),Screen(.7,.67,1,1),Screen(.3,.3,.7,.7)]:
        rw, rh, budget, offset = reference.geometry(picture.width,picture.height,screen,5000)
        dx, dy = offset % picture.width, offset // picture.width
        n = reference.capture(bytes(packed),0,4*w,rw,rh,budget,4*((picture.y+dy)*w+picture.x+dx))
        reference.vm.mem_write(ZONE, bytes(0x64))
        expected = reference.accept()
        zone, count = Zone(), ct.c_uint()
        assert lib.c9_video_ingest_picture(ct.byref(zone),ct.byref(frame),picture,screen,5000,ct.byref(count))
        assert count.value == n and (zone.trimean.values(),zone.average.values()) == expected
    print("Four zone targets matched desktop CPU sampling inside an odd-aligned NV12 crop.")


def main():
    desktop = Detector(sys.argv[1])
    lib = ct.CDLL(str(Path(sys.argv[2]).resolve()))
    state = State()
    lib.c9_bars_init(ct.byref(state))
    arm = ArmBitmap(sys.argv[3])
    arm_state, arm_out = 0x1024000, 0x1021000
    arm.vm.mem_write(arm_state, bytes(state))
    crops = set()
    for i, (frame, raw, uv, packed, layout) in enumerate(images()):
        expected, raw_probe = desktop.detect(packed, frame.width, frame.height, layout)
        result = Region()
        assert lib.c9_active_picture(ct.byref(state), ct.byref(frame), ct.byref(result))
        actual = (result.x, result.y, result.width, result.height)
        last = (state.pos-1) % 201
        probe = (state.horizontal.observed[last], state.vertical.observed[last])
        assert (actual, probe) == (expected, raw_probe), (i, actual, expected, probe, raw_probe)
        frame_addr = arm.load_frame(frame, raw, uv)
        assert arm.call("c9_active_picture", [arm_state, frame_addr, arm_out, 0])
        assert struct.unpack("<4I", arm.vm.mem_read(arm_out, 16)) == expected
        crops.add(actual)
    assert (5,3,55,37) in crops and len(crops) > 5
    print(f"{i+1} desktop frame probes and crops matched, with {len(crops)} distinct crops; linked ARM crops matched throughout.")
    cropped_sampling(sys.argv[1], lib)


if __name__ == "__main__":
    main()
