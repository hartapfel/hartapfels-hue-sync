"""Compare the desktop's ordinary Video AreaEffect with the worker's opaque layer."""
import itertools
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE
from unicorn import x86_const as reg
from desktop_serializer import DesktopSerializer, IMAGE_BASE


class DesktopArea(DesktopSerializer):
    def __init__(self, exe):
        super().__init__(exe)
        data = Path(exe).read_bytes()
        vm = self.vm
        mapped = {start for start, _, _ in vm.mem_regions()}

        def page(rva, offset, flags):
            if IMAGE_BASE + rva not in mapped:
                vm.mem_map(IMAGE_BASE + rva, 4096, flags)
            vm.mem_write(IMAGE_BASE + rva, data[rva - offset:rva - offset + 4096])

        for rva in (0x17000, 0x1b000, 0x2b000, 0x3a000, 0x3b000, 0xc3000,
                    0x1bc000, 0x1bd000, 0x1c0000, 0x1c9000, 0x1cb000, 0x1cc000, 0x1cd000, 0x1ce000, 0x1cf000,
                    0x1e4000, 0x1e6000, 0x1e7000, 0x1f6000, 0x38f000):
            page(rva, 0xc00, UC_PROT_READ | UC_PROT_EXEC)
        for rva in (0x3ca000, 0x1317000, 0x1319000):
            page(rva, 0x1600, UC_PROT_READ)
        page(0x1406000, 0x2200, UC_PROT_READ)
        self.heap = 0x40000
        vm.mem_map(self.heap, 0x100000, UC_PROT_READ | UC_PROT_WRITE)

        # Keep desktop allocations inside the emulator. Freed blocks can stay in
        # the bounded test arena until the comparison finishes.
        def allocator(machine, address, _size, _context):
            if address == IMAGE_BASE + 0x38f470:
                size = machine.reg_read(reg.UC_X86_REG_RCX)
                machine.reg_write(reg.UC_X86_REG_RAX, self.heap)
                self.heap += (size + 15) & ~15
                assert self.heap <= 0x140000
            sp = machine.reg_read(reg.UC_X86_REG_RSP)
            ret, = struct.unpack("<Q", machine.mem_read(sp, 8))
            machine.reg_write(reg.UC_X86_REG_RSP, sp + 8)
            machine.reg_write(reg.UC_X86_REG_RIP, ret)

        for rva in (0x38f470, 0x38f4ac):
            vm.hook_add(UC_HOOK_CODE, allocator, begin=IMAGE_BASE + rva, end=IMAGE_BASE + rva)

        self.effect, self.color, self.output = 0x11000, 0x11800, 0x11900
        self.light, self.light_pointer = 0x12000, 0x12500
        name, cuboid, entries, vtable = 0x12600, 0x12700, 0x12800, 0x12900
        # Video initialization passes zero for layer and opacity-from-brightness.
        vm.mem_write(name, struct.pack("<16sQQ", b"Video Area", 10, 15))
        self._call(0x1cdb00, (self.effect, name, 0, 0))
        areas, = struct.unpack("<Q", vm.mem_read(self.effect + 0xd0, 8))
        vm.mem_write(areas, struct.pack("<3Q", entries, entries + 16, entries + 16))
        vm.mem_write(entries, struct.pack("<QQ", cuboid, 0))
        vm.mem_write(cuboid, struct.pack("<QQ3dQ3dB", vtable, 0, -1., 1., 1., 0, 1., -1., -1., 0))
        vm.mem_write(vtable + 0x38, struct.pack("<Q", IMAGE_BASE + 0x1cef20))
        vm.mem_write(self.light_pointer, struct.pack("<QQ", self.light, 0))

    def set_color(self, rgb):
        for register, value in zip((reg.UC_X86_REG_XMM1, reg.UC_X86_REG_XMM2, reg.UC_X86_REG_XMM3), rgb):
            self.vm.reg_write(register, int.from_bytes(struct.pack("<d", value), "little"))
        self._call(0x1cbd90, (self.color,))
        self._call(0x1cd480, (self.effect, self.color))

    def render(self, position):
        self.vm.mem_write(self.light + 0xe8, struct.pack("<Q3d", 0, *position))
        self._call(0x1cdd70, (self.effect, self.output, self.light_pointer))
        return struct.unpack("<4d", self.vm.mem_read(self.output + 8, 32))


def main():
    desktop = DesktopArea(sys.argv[1])
    rng = random.Random(221)
    colors = list(itertools.product((0., 1/255, 0.18, 0.5, 1.), repeat=3))
    colors.extend(tuple(rng.random() for _ in range(3)) for _ in range(100))
    for rgb in colors:
        desktop.set_color(rgb)
        for position in [(0., 0., 0.), (-1., 1., 0.), (1.01, 0., 0.)]:
            layer = desktop.render(position)
            expected = (*rgb, 1.) if position[0] <= 1 else (0., 0., 0., 0.)
            assert layer == expected, (rgb, position, layer)
            channel = desktop.channel(3, desktop.mix([layer]))
            assert channel == struct.pack(">BHHH", 3, *(int(x * 65535) for x in expected[:3]))
    print(f"{len(colors) * 3} desktop area renders matched opacity, RGB and serialized channel bytes.")


if __name__ == "__main__":
    main()
