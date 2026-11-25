"""Generate an HDR10 reference clip and verify its decoded patch values."""
import argparse
import math
from pathlib import Path
import struct
import subprocess
import tempfile


WIDTH, HEIGHT = 1920, 1080
GRAY_NITS = (0, .01, .1, 1, 5, 10, 18, 50, 100, 203, 400, 1000)
PATCHES = [(n, n, n) for n in (0, 203, 0, 203, 203, 0, *GRAY_NITS)]
PATCHES += [(203, 0, 0), (0, 203, 0), (0, 0, 203), (0, 203, 203), (203, 0, 203), (203, 203, 0)]


def pq(nits):
    power = (nits / 10000) ** (2610 / 16384)
    return ((3424 / 4096 + 2413 / 128 * power) / (1 + 2392 / 128 * power)) ** (2523 / 32)


def yuv10(rgb):
    r, g, b = map(pq, rgb)
    y = .2627 * r + .6780 * g + .0593 * b
    return (round(64 + 876 * y), round(512 + 896 * (b - y) / 1.8814),
            round(512 + 896 * (r - y) / 1.4746))


def chart_frame():
    codes = list(map(yuv10, PATCHES))
    planes = []
    for component, scale in ((0, 1), (1, 2), (2, 2)):
        for row in range(4):
            line = b"".join(struct.pack("<H", p[component]) * (WIDTH // (6 * scale))
                            for p in codes[row * 6:row * 6 + 6])
            planes.append(line * (HEIGHT // (4 * scale)))
    return b"".join(planes)


def generate(ffmpeg, output):
    raw = chart_frame()
    average = math.ceil(sum(.2627*r + .678*g + .0593*b for r, g, b in PATCHES) / len(PATCHES))
    master = "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,1)"
    params = (f"hdr10=1:repeat-headers=1:master-display={master}:max-cll=1000,{average}"
              ":qp=0:ipratio=1:pbratio=1:sao=0:deblock=0:pools=2:frame-threads=2")
    # One static source frame covers every instant, so both HDMI and USB
    # playback can be sampled without having to synchronize playback clocks.
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        clip = Path(work) / "chart.mp4"
        tags = ["-color_range", "tv", "-colorspace", "bt2020nc", "-color_trc", "smpte2084",
                "-color_primaries", "bt2020"]
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p10le",
                        "-s", f"{WIDTH}x{HEIGHT}", "-r", "24", *tags, "-i", "pipe:0",
                        "-vf", "tpad=stop_mode=clone:stop_duration=15", "-frames:v", "360",
                        "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", params,
                        *tags, "-tag:v", "hvc1", "-movflags", "+faststart",
                        str(clip)], input=raw, check=True)
        for second in (0, 7, 14):
            decoded = subprocess.run([ffmpeg, "-loglevel", "error", "-ss", str(second), "-i", str(clip),
                                      "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p10le", *tags, "-"],
                                     check=True, capture_output=True).stdout
            assert len(decoded) == len(raw)
            offset = 0
            for scale in (1, 2, 2):
                w, h = WIDTH // scale, HEIGHT // scale
                for i in range(24):
                    x, y = ((i % 6) * 2 + 1) * w // 12, ((i // 6) * 2 + 1) * h // 8
                    at = offset + (y * w + x) * 2
                    source_code, = struct.unpack_from("<H", raw, at)
                    decoded_code, = struct.unpack_from("<H", decoded, at)
                    assert abs(source_code - decoded_code) <= 1, (second, i, scale, source_code, decoded_code)
                offset += w * h * 2
        clip.replace(output)
    print(f"All 24 decoded Y/Cb/Cr centers matched within one 10-bit code at 0, 7 and 14 seconds in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ffmpeg")
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    generate(options.ffmpeg, options.output.resolve())
