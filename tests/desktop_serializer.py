"""Execute the desktop serializer's RGB record path on a reserved local buffer."""
import struct

from unicorn import UC_PROT_EXEC, UC_PROT_READ
from unicorn import x86_const as reg
from desktop_bitmap import DesktopBitmap, IMAGE_BASE
from pathlib import Path


class DesktopSerializer(DesktopBitmap):
    def __init__(self, exe):
        super().__init__(exe)
        data = Path(exe).read_bytes()
        for page in (0x214000, 0x1cb000, 0x1bc000, 0x2b000):
            self.vm.mem_map(IMAGE_BASE + page, 4096, UC_PROT_READ | UC_PROT_EXEC)
        for start, end in ((0x21455a, 0x214562), (0x214620, 0x21480f), (0x1cbf40, 0x1cbfb4),
                           (0x2b3a0, 0x2b3a5), (0x1bcdc0, 0x1bcdd5)):
            self.vm.mem_write(IMAGE_BASE + start, data[start - 0xc00:end - 0xc00])
        for rva in (0x131a2e0, 0x3ca9b0):
            self.vm.mem_map(IMAGE_BASE + (rva & ~4095), 4096, UC_PROT_READ)
            self.vm.mem_write(IMAGE_BASE + rva, data[rva - 0x1600:rva - 0x1600 + 8])

    def channel(self, channel_id, rgb):
        vm = self.vm
        frame, vector, output = 0x1e000, 0x1d000, 0x1d100
        vm.reg_write(reg.UC_X86_REG_RBP, frame)
        vm.reg_write(reg.UC_X86_REG_RSP, frame - 0xa0)
        vm.reg_write(reg.UC_X86_REG_RDI, vector)
        vm.reg_write(reg.UC_X86_REG_RSI, channel_id)
        vm.mem_write(vector, struct.pack("<3Q", output, output, output + 7))
        vm.mem_write(frame - 0x31, struct.pack("<4d", *rgb, 1.))
        vm.mem_write(frame + 0x67, struct.pack("<Q", 0x1d200))
        vm.mem_write(0x1d208, struct.pack("<Q", 0x1d240))
        vm.mem_write(0x1d258, b"\x01")  # HueStream major version 2
        vm.emu_start(IMAGE_BASE + 0x21455a, IMAGE_BASE + 0x214562, count=1)
        vm.emu_start(IMAGE_BASE + 0x214620, IMAGE_BASE + 0x21480f, count=300)
        assert vm.reg_read(reg.UC_X86_REG_RIP) == IMAGE_BASE + 0x21480f
        assert struct.unpack("<Q", vm.mem_read(vector + 8, 8))[0] == output + 7
        return bytes(vm.mem_read(output, 7))
