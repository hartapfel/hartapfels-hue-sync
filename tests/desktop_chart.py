"""Compute Hue Desktop light commands for the chart used by the ARM package test."""

from desktop_video import DesktopVideo, Screen, ZONE
from hdr_chart import PATCHES, yuv10
from yuv import decode


def chart_commands(exe, core, ticks, effect, threshold):
    presets = core["CaptureOption"]
    preset, = [p for p in presets if p["Name"] == "Video Default" and p["ScreenCount"] == 1]
    assert len(preset["Areas"]) == 14
    # Reference the RGB bytes presented to the desktop analyzer. The TV
    # fixture receives padded NV12 with the worker's configured BT.709 range.
    colors = [bytes((*decode(*(round(c/4) for c in yuv10(p)), 1, False), 255)) for p in PATCHES]
    width, height = 96, 64
    pixels = b"".join(colors[(y//16)*6 + x//16] for y in range(height) for x in range(width))
    desktop = DesktopVideo(exe)
    channels, first, settled = [], bytearray(), bytearray()
    for i, area in enumerate(preset["Areas"]):
        rect, room = area["ScreenPos"], area["HueArea"]
        screen = Screen(*(rect[k] for k in ("X0", "Y0", "X1", "Y1")))
        position = [(room[a+"0"] + room[a+"1"])/2 for a in "XYZ"]
        identifier = 100-i
        channels.append(dict(zip(("id", "x", "y", "z"), (identifier, *position))))
        desktop.vm.mem_write(ZONE, bytes(0x64))
        w, h, budget, offset = desktop.geometry(width, height, screen, 5000)
        desktop.capture(pixels, 0, 4*width, w, h, budget, 4*offset)
        target, _average = desktop.accept(threshold)
        for records, color in zip((first, settled), ticks(target)):
            effect.set_color(color)
            records.extend(effect.channel(identifier, effect.mix([effect.render(position)])))
    return dict(channels=channels, first=first.hex(), settled=settled.hex())
