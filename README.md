# C9 Screen Sync

This standalone webOS project is porting the video path from the locally installed Hue Sync Desktop 1.13.1.83. The target is an LG C9 using a local video capture service and a Hue Entertainment area. The current build produces a processing library.

## Desktop comparisons

The reference executable has SHA-256 `2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad`. The test harness reads selected numeric routines from the user's copy into an offline CPU emulator. The tests generate all image inputs locally.

* The CPU bitmap analyzer samples a rectangular grid with a fixed budget. It returns two RGB colors per region, an integer mean and a per-channel trimean. The port accepts strided 8-bit RGBX/BGRX and packed 10-bit RGB. Black/white distributions, random colors, odd/even sample counts, and all three formats matched the reference grid and reduction in 42 cases.
* The video interpolation loop converts a region color to CIELAB, updates lightness and chroma with separate coefficients, and converts back to sRGB. The ordinary filtering and color transforms matched the reference instructions in 750 comparisons on Windows x64. The optical result depends on stages before and after these operations.
* Another 352 comparisons exercise successive frames for one light in Video mode at Moderate intensity. They cover gain, peak limiting, temporal filtering and the output black cutoff, with audio and background lighting disabled. Each output channel matched the desktop calculation within 0.00001 on a scale from zero to one.
* The installed configuration selects CPU sampling. There is a separate OpenCL kernel in the executable. Its sampling math differs from the CPU path.

On Windows with CMake, a C compiler, and Python with `unicorn==2.1.4` installed, run the following from this directory.

```powershell
cmake -S . -B build
cmake --build build --config Release
python tests/desktop_bitmap.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll
python tests/desktop_color.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll
```

## Integration status

Full-frame comparisons still need the desktop region mapping, black-bar handling and Entertainment output. Capture-side HDR encoding on the C9 needs its own check. An installer, measured capture cost, and opt-in boot and standby recovery are pending. PicCap's underlying capture project supplies the C9-generation video backend and frame plane/stride API. [webOS capture backend](https://github.com/webosbrew/hyperion-webos)
