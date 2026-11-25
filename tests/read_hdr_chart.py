"""Compare raw C9 patch codes with an unchanged HDR10 signal in two 8-bit ranges."""
import json
import math
from pathlib import Path
import sys
from hdr_chart import GRAY_NITS, PATCHES, pq, yuv10


def reference(patch, layout, limited):
    if layout == "RGB24":
        base, span = (16, 219) if limited else (0, 255)
        return [base + span*pq(n) for n in patch]
    y, cb, cr = yuv10(patch)
    if limited:
        return [y/4, cb/4, cr/4]
    return [(y-64)*255/876, 128 + (cb-512)*255/896, 128 + (cr-512)*255/896]


def summarize(report):
    layout = report["format"]
    patches = report["patches"]
    if (layout not in ("RGB24", "NV12", "NV16") or report["chart"] != "c9-hdr10-v1" or
            report["frames"] != 8 or len(patches) != len(PATCHES) or
            any(len(p) != 3 or any(type(code) not in (int, float) or not 0 <= code <= 255 for code in p)
                for p in patches)):
        raise ValueError("Expected the 24 raw patch triples from --measure-hdr-chart")
    print(f"{report['width']} x {report['height']} {layout}, averaged over {report['frames']} frames")
    print("Source nits    Raw gray (R G B)" if layout == "RGB24" else "Source nits    Raw gray (Y Cb Cr)")
    for nits, values in zip(GRAY_NITS, patches[6:18]):
        print(f"{nits:11g}    " + " ".join(f"{code:6.2f}" for code in values))
    for limited in (True, False):
        label = "limited" if limited else "full"
        errors = [a-b for observed, source in zip(patches[6:], PATCHES[6:])
                  for a, b in zip(observed, reference(source, layout, limited))]
        rms = math.sqrt(sum(error*error for error in errors) / len(errors))
        print(f"Unchanged PQ/BT.2020 ({label} range): RMS {rms:.2f}, max {max(map(abs,errors)):.2f} codes")


if __name__ == "__main__":
    summarize(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig")))
