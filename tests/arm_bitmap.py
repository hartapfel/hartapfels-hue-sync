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
        self.symbols = {}
        for pos in range(symbols[4], symbols[4] + symbols[5], symbols[9]):
            name, address = struct.unpack_from("<II", elf, pos)
            if address:
                end = elf.index(0, strings + name)
                self.symbols[elf[strings + name:end].decode()] = address
        self.vm.reg_write(UC_ARM_REG_C1_C0_2, self.vm.reg_read(UC_ARM_REG_C1_C0_2) | (15 << 20))
        self.vm.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
        self.vm.mem_map(0x1000000, 0x40000)

    def load_frame(self, frame, pixels, uv):
        frame_addr, pixels_addr, uv_addr = 0x1000000, 0x1001000, 0x1011000
        assert len(pixels) <= 0x10000 and len(uv) <= 0x10000
        self.vm.mem_write(pixels_addr, pixels)
        self.vm.mem_write(uv_addr, uv)
        self.vm.mem_write(frame_addr, struct.pack("<11I", frame.width, frame.height, frame.layout,
            pixels_addr, frame.pixels.available, frame.pixels.stride, uv_addr, frame.chroma.available,
            frame.chroma.stride, frame.matrix, frame.range))
        return frame_addr

    def call(self, symbol, words):
        end, sp = 0x1022000, 0x103ff00
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), words):
            self.vm.reg_write(reg, value)
        self.vm.reg_write(UC_ARM_REG_SP, sp)
        self.vm.reg_write(UC_ARM_REG_LR, end)
        self.vm.mem_write(sp, struct.pack(f"<{len(words)-4}I", *words[4:]))
        self.vm.emu_start(self.symbols[symbol], end, count=1000000)
        if self.vm.reg_read(UC_ARM_REG_PC) != end:
            raise RuntimeError(f"ARM {symbol} exceeded the instruction budget")
        return self.vm.reg_read(UC_ARM_REG_R0)

    def run(self, frame, area, budget, pixels, uv):
        frame_addr = self.load_frame(frame, pixels, uv)
        result = 0x1021000
        # AAPCS splits the by-value four-word region between r1-r3 and the stack.
        assert self.call("analyze_region", [frame_addr, area.x, area.y, area.width, area.height, budget, result])
        return struct.unpack("<7I", self.vm.mem_read(result, 28))

    def ingest(self, frame, screen, budget, pixels):
        frame_addr = self.load_frame(frame, pixels, b"")
        result, state = 0x1021000, 0x1021100
        coords = struct.unpack("<4I", bytes(screen))
        assert self.call("c9_video_ingest", [state, frame_addr, *coords, budget, result])
        rgb = struct.unpack("<6f", self.vm.mem_read(state, 24))
        count, = struct.unpack("<I", self.vm.mem_read(result, 4))
        return (rgb[:3], rgb[3:]), count

    def area_mask(self, point):
        return self.call("c9_video_area_mask", struct.unpack("<6I", struct.pack("<3d", *point)))
