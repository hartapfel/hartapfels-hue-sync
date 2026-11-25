"""Compare direct strided capture-plane analysis to decoded RGB at desktop sample positions."""
import ctypes as ct
import random
import sys

from desktop_bitmap import DesktopBitmap
from arm_bitmap import ArmBitmap
from yuv import decode


class Plane(ct.Structure):
    _fields_ = [("data", ct.c_char_p), ("available", ct.c_size_t), ("stride", ct.c_size_t)]


class Frame(ct.Structure):
    _fields_ = [("width", ct.c_uint), ("height", ct.c_uint), ("layout", ct.c_int),
                ("pixels", Plane), ("chroma", Plane), ("matrix", ct.c_int), ("range", ct.c_int)]


class Region(ct.Structure):
    _fields_ = [(field, ct.c_uint) for field in ("x", "y", "width", "height")]


def main():
    reference = DesktopBitmap(sys.argv[1])
    arm = ArmBitmap(sys.argv[3]) if len(sys.argv) > 3 else None
    analyze = ct.CDLL(sys.argv[2]).analyze_region
    analyze.argtypes = [ct.POINTER(Frame), Region, ct.c_uint, ct.POINTER(ct.c_uint32)]
    width, height, budget = 37, 23, 61
    rng = random.Random(308)
    regions = [Region(0, 0, width, height), Region(1, 3, 13, 15), Region(34, 20, 3, 3)]
    cases = 0
    for layout in (3, 4, 5):
        step = 3 if layout == 3 else 1
        stride = width * step + 5
        uv_stride = (width + 1) // 2 * 2 + 9
        uv_rows = (height + 1) // 2 if layout == 4 else height
        pixels = rng.randbytes((height - 1) * stride + width * step)
        chroma = rng.randbytes((uv_rows - 1) * uv_stride + (width + 1) // 2 * 2)
        frame = Frame(width, height, layout, Plane(pixels, len(pixels), stride),
                      Plane(chroma, len(chroma), uv_stride), 0, 0)
        for matrix in range(1 if layout == 3 else 3):
            for full in range(1 if layout == 3 else 2):
                frame.matrix, frame.range = matrix, full
                for area in regions:
                    samples = []
                    for x, y in reference.grid(area.width, area.height, budget):
                        x += area.x
                        y += area.y
                        start = y * stride + x * step
                        if layout == 3:
                            rgb = pixels[start:start + 3]
                        else:
                            row = y // 2 if layout == 4 else y
                            uv = row * uv_stride + x // 2 * 2
                            rgb = decode(pixels[start], chroma[uv], chroma[uv + 1], matrix, full)
                        samples.append(rgb)
                    expected = reference.reduce(samples, 8)
                    output = (ct.c_uint32 * 7)()
                    assert analyze(ct.byref(frame), area, budget, output)
                    assert tuple(output) == expected, (layout, matrix, full, tuple(output), expected)
                    if arm:
                        assert arm.run(frame, area, budget, pixels, chroma) == expected
                    cases += 1
        assert not analyze(ct.byref(frame), Region(width, 0, 1, 1), budget, output)
        frame.pixels.available -= 1
        assert not analyze(ct.byref(frame), regions[0], budget, output)
        frame.pixels.available += 1
        if layout != 3:
            frame.chroma.available -= 1
            assert not analyze(ct.byref(frame), regions[0], budget, output)
    print(f"{cases} capture-region comparisons passed, plus plane and region bounds checks.")
    if arm:
        print(f"The linked ARMv7 analyzer matched all {cases} references.")


if __name__ == "__main__":
    main()
