"""Compile the fixed scalar color blocks of the locally installed Hue Sync 1.13.1.83.

Only the low float lane is live in these blocks. Bind their live registers and
specific stack/structure fields to the color.h API. The native compiler can then
allocate registers and optimize the forward branches for the target CPU.
"""
import argparse
import hashlib
from pathlib import Path
import re
import struct
import subprocess


IMAGE = 0x140000000
DIGEST = "2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad"
POWF = IMAGE + 0x39075A
INSN = re.compile(r"^\s*([\da-f]+): ((?:[\da-f]{2} )+)\s*([a-z]+)\s+(.+)$")
REGISTER = re.compile(r"xmm(1[0-5]|[0-9])$")


def instructions(objdump, exe, image, start, end):
    command = [objdump, "-d", "--x86-asm-syntax=intel", f"--start-address={IMAGE+start:#x}",
               f"--stop-address={IMAGE+end:#x}", exe]
    listing = subprocess.check_output(command, text=True)
    cursor = start
    result = []
    for line in listing.splitlines():
        match = INSN.match(line)
        if not match:
            continue
        address, raw, op, operands = match.groups()
        raw = bytes.fromhex(raw)
        # The file and section offsets belong to the SHA-256-pinned PE.
        if int(address, 16) != IMAGE + cursor or image[cursor-0xc00:cursor-0xc00+len(raw)] != raw:
            raise ValueError("Disassembly differs from the pinned code section")
        result.append((cursor, op, operands.strip()))
        cursor += len(raw)
    if cursor != end:
        raise ValueError(f"Incomplete disassembly of block {start:#x}")
    return result


def branch_target(operands):
    return int(operands.split()[0], 16) - IMAGE


def emit_block(code, image, fields):
    targets = {branch_target(args) for _, op, args in code if op.startswith("j")}
    if not targets.issubset({address for address, _, _ in code}):
        raise ValueError("Color branch leaves the selected block")

    def operand(text):
        if REGISTER.fullmatch(text):
            return text
        if text in fields:
            return fields[text]
        constant = re.fullmatch(r"dword ptr \[rip \+ 0x[0-9a-f]+\]\s*#\s*(0x[0-9a-f]+)", text)
        if constant:
            rva = int(constant[1], 16) - IMAGE
            if not 0x3c2000 <= rva < 0x3cb000:
                raise ValueError("Unexpected color constant location")
            number, = struct.unpack_from("<f", image, rva - 0x1600)
            return number.hex() + "f"
        raise ValueError(f"Unsupported color operand {text}")

    statements = []
    compare = None
    for address, op, args in code:
        if address in targets:
            statements.append(f"color_{address:x}:")
        if op in ("movss", "movaps", "addss", "subss", "mulss", "divss", "comiss"):
            left, right = (operand(v) for v in args.split(", "))
            if op == "comiss":
                compare = (left, right)
                continue
            operator = {"movss":"=", "movaps":"=", "addss":"+=", "subss":"-=", "mulss":"*=", "divss":"/="}[op]
            statement = f"{left} {operator} {right};"
        elif op == "test" and args == "dil, dil":
            compare = "encoded"
            continue
        elif op in ("jb", "jbe") and isinstance(compare, tuple):
            # COMISS sets carry and zero for unordered operands as well.
            relation = ">=" if op == "jb" else ">"
            statement = f"if (!({compare[0]} {relation} {compare[1]})) goto color_{branch_target(args):x};"
        elif op == "je" and compare == "encoded":
            statement = f"if (!srgb_encoded) goto color_{branch_target(args):x};"
        elif op == "jmp":
            statement = f"goto color_{branch_target(args):x};"
        elif op == "call" and branch_target(args) == POWF - IMAGE:
            statement = "xmm0 = powf(xmm0, xmm1);"
        else:
            raise ValueError(f"Unsupported color instruction at {address:#x}: {op}")
        compare = None
        statements.append("    " + statement)
    return statements


def generate(exe, objdump):
    image = exe.read_bytes()
    if hashlib.sha256(image).hexdigest() != DIGEST:
        raise ValueError("This build requires the Windows x64 Hue Sync Desktop 1.13.1.83 executable")
    source = ['#include "color.h"', "#include <math.h>", ""]

    def function(signature, ranges, registers, fields, returns, setup=()):
        code = [insn for start, end in ranges for insn in instructions(objdump, exe, image, start, end)]
        used = set(re.findall(r"\bxmm\d+\b", " ".join(args for _, _, args in code)))
        source.extend((signature, "{", *("    " + line for line in setup)))
        for reg in sorted(used, key=lambda s: int(s[3:])):
            init = " = " + registers[reg] if reg in registers else ""
            source.append(f"    float {reg}{init};")
        source.extend(emit_block(code, image, fields))
        source.extend(("    return " + returns + ";", "}", ""))

    # The ordinary Video preset disables the optional background-light branch.
    function("struct c9_lab c9_rgb_to_lab(struct c9_rgb rgb, int srgb_encoded)",
             [(0x171843, 0x171ac9)],
             {"xmm14":"rgb.r", "xmm13":"rgb.g", "xmm11":"rgb.b",
              "xmm9":"0.055f", "xmm10":"1.f", "xmm12":"0.f"}, {},
             "(struct c9_lab){xmm7, xmm9, xmm8}")
    function("struct c9_lab c9_filter_lab(struct c9_lab previous, struct c9_lab next, float alpha_l, float alpha_ab)",
             [(0x171ccc, 0x171d20)],
             {"xmm0":"alpha_l", "xmm15":"alpha_ab", "xmm7":"next.l",
              "xmm9":"next.a", "xmm8":"next.b", "xmm10":"1.f"},
             {"dword ptr [r14 + 0x50]":"previous.l", "dword ptr [r14 + 0x54]":"previous.a",
              "dword ptr [r14 + 0x58]":"previous.b"}, "previous")
    function("struct c9_rgb c9_lab_to_rgb(struct c9_lab lab)", [(0x171d4c, 0x171f91)],
             {"xmm4":"lab.l", "xmm7":"lab.a", "xmm0":"lab.b", "xmm10":"1.f", "xmm12":"0.f"},
             {"dword ptr [rsp + 0x34]":"rgb.r", "dword ptr [rsp + 0x38]":"rgb.g",
              "dword ptr [rsp + 0x30]":"rgb.b"}, "rgb", ["struct c9_rgb rgb;"])
    function("struct c9_smoothing c9_video_smoothing(float fps, float alpha_l, float alpha_ab)",
             [(0x171507, 0x171513), (0x171523, 0x17155f)], {"xmm6":"fps", "xmm10":"1.f"},
             {"dword ptr [r15 + 4*rbx + 0x48]":"alpha_l", "dword ptr [r15 + 4*rbx + 0x44]":"alpha_ab",
              "dword ptr [rsp + 0x44]":"lightness"}, "(struct c9_smoothing){lightness, xmm1}",
             ["if (!(fps > 0.f)) return (struct c9_smoothing){0.f, 0.f};", "float lightness;"])
    return "\n".join(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("objdump", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(generate(args.exe, args.objdump), encoding="ascii")
