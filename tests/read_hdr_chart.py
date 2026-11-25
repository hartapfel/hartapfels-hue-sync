"""Compare a C9 capture with the HDR10 source and an optional measured Windows desktop."""
import argparse
import json
import math
from pathlib import Path
from hdr_chart import GRAY_NITS, PATCHES, pq, yuv10
from yuv import decode


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


def compare_desktop(tv, desktop):
    # Remove the repeated marker row from the error totals. Give gray and
    # colored patches separate scores so transfer and chroma errors are visible.
    bits = {24: 1023, 28: 255, 29: 255, 87: 255, 91: 255}
    maximum = bits.get(desktop.get("dxgiFormat"))
    patches = desktop.get("patches", [])
    if (not maximum or desktop.get("codeMax") != maximum or desktop.get("chart") != "c9-hdr10-v1" or
            desktop.get("source") != "desktop-duplication" or desktop.get("displayColorSpace") != 12 or
            desktop.get("frames", 0) < 3 or len(patches) != len(PATCHES) or
            any(len(p) != 3 or any(type(c) is not int or not 0 <= c <= maximum for c in p) for p in patches)):
        raise ValueError("Expected measured patch codes from the Windows HDR desktop chart")
    target = [[c * 255 / maximum for c in p] for p in patches[6:]]
    observed = tv["patches"][6:]
    candidates = {}
    if tv["format"] == "RGB24":
        candidates["RGB full"] = observed
        candidates["RGB limited"] = [[max(0, min(255, (c-16)*255/219)) for c in p] for p in observed]
    else:
        for i, matrix in enumerate(("BT.601", "BT.709", "BT.2020")):
            for full in (False, True):
                name = matrix + (" full" if full else " limited")
                candidates[name] = [decode(*p, i, full) for p in observed]
    scores = {}
    for name, colors in candidates.items():
        errors = [[a-b for a, b in zip(color, ref)] for color, ref in zip(colors, target)]
        def rms(group):
            return math.sqrt(sum(x*x for p in group for x in p) / (3 * len(group)))
        scores[name] = (rms(errors[:12]), rms(errors[12:]), max(abs(x) for p in errors for x in p))
    return scores


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement", type=Path)
    parser.add_argument("--desktop", type=Path, help="JSON from c9-desktop-chart --measure")
    args = parser.parse_args()
    report = json.loads(args.measurement.read_text(encoding="utf-8-sig"))
    summarize(report)
    if args.desktop:
        desktop = json.loads(args.desktop.read_text(encoding="utf-8-sig"))
        scores = compare_desktop(report, desktop)
        print("Difference from measured Windows desktop in 8-bit RGB codes")
        print("Decoder            Gray RMS    Color RMS     Maximum")
        for name, (gray, color, peak) in scores.items():
            print(f"{name:16s} {gray:10.2f} {color:12.2f} {peak:11.2f}")
