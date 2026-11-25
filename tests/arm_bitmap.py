"""Run the linked ARMv7 capture analyzer in isolation, without TV I/O."""
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import (
    UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
)


class ArmBitmap:
    def __init__(self, path):
        elf = Path(path).read_bytes()
        if elf[:6] != b'\x7fELF\x01\x01' or struct.unpack_from("<HH", elf, 16) != (2, 40):
            raise ValueError("Expected a little-endian ARM ELF32 executable")
        self.vm = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        phoff, shoff = struct.unpack_from("<II", elf, 28)
        phsize, phnum, shsize, shnum = struct.unpack_from("<4H", elf, 42)
        for i in range(phnum):
            kind, offset, address, _, size, memsize = struct.unpack_from("<6I", elf, phoff + i * phsize)
            if kind == 1 and memsize:
                start = address & ~4095
                end = (address + memsize + 4095) & ~4095
                self.vm.mem_map(start, end - start)
                self.vm.mem_write(address, elf[offset:offset + size])
        sections = [struct.unpack_from("<10I", elf, shoff + i * shsize) for i in range(shnum)]
        symbols = next(section for section in sections if section[1] == 11)  # SHT_DYNSYM
        strings = sections[symbols[6]][4]
        for pos in range(symbols[4], symbols[4] + symbols[5], symbols[9]):
            name, address = struct.unpack_from("<II", elf, pos)
            if elf.startswith(b"analyze_region\0", strings + name):
                self.analyze = address
                break
        else:
            raise ValueError("Missing analyze_region export")
        self.vm.reg_write(UC_ARM_REG_C1_C0_2, self.vm.reg_read(UC_ARM_REG_C1_C0_2) | (15 << 20))
        self.vm.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
        self.vm.mem_map(0x1000000, 0x40000)

    def run(self, frame, area, budget, pixels, uv):
        frame_addr, pixels_addr, uv_addr = 0x1000000, 0x1001000, 0x1011000
        result, end, sp = 0x1021000, 0x1022000, 0x103fff0
        assert len(pixels) <= 0x10000 and len(uv) <= 0x10000
        self.vm.mem_write(pixels_addr, pixels)
        self.vm.mem_write(uv_addr, uv)
        self.vm.mem_write(frame_addr, struct.pack("<11I", frame.width, frame.height, frame.layout,
            pixels_addr, frame.pixels.available, frame.pixels.stride, uv_addr, frame.chroma.available,
            frame.chroma.stride, frame.matrix, frame.range))
        # AAPCS splits the by-value four-word region between r1-r3 and the stack.
        for reg, value in [(UC_ARM_REG_R0, frame_addr), (UC_ARM_REG_R1, area.x),
                           (UC_ARM_REG_R2, area.y), (UC_ARM_REG_R3, area.width),
                           (UC_ARM_REG_SP, sp), (UC_ARM_REG_LR, end)]:
            self.vm.reg_write(reg, value)
        self.vm.mem_write(sp, struct.pack("<III", area.height, budget, result))
        self.vm.emu_start(self.analyze, end, count=500000)
        if self.vm.reg_read(UC_ARM_REG_PC) != end or self.vm.reg_read(UC_ARM_REG_R0) != 1:
            raise RuntimeError("ARM analyzer failed or exceeded the instruction budget")
        return struct.unpack("<7I", self.vm.mem_read(result, 28))
