# C9 Screen Sync

This standalone webOS project is porting the video path from the locally installed Hue Sync Desktop 1.13.1.83. The target is an LG C9 using local video capture and a Hue Entertainment area. The current build produces a processing library and an ARM video capture checker.

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

## C9 video capture

The capture adapter loads the DILE video backend used by PicCap 0.5.3 and borrows each frame until analysis finishes. It supports RGB24, NV12 and NV16. Only pixels on the sampling grid undergo Y′CbCr decoding. Each region retains its position in the original chroma planes, including regions that start on an odd row or column. The matrix and video range are explicit inputs. Primaries and transfer-function conversion for HDR are still pending. [PicCap 0.5.3](https://github.com/TBSniller/piccap/releases/tag/0.5.3)

With Zig 0.13.0 and a checkout of `webosbrew/hyperion-webos` at `00c932f092084b234b3f619b060a98fa134c12bd`, the following PowerShell commands cross-compile the checker locally and test the linked ARM code. Set `$piccap` to that checkout.

```powershell
zig cc -target arm-linux-gnueabi.2.17 -mcpu=cortex_a9 -O2 -ffp-contract=off '-Wl,--export-dynamic' "-I$piccap/unicapture" "-I$piccap/src" src/capture.c src/capture_check.c src/bitmap.c -ldl -lm -o build/c9-capture-check
python tests/capture_bitmap.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll build/c9-capture-check
```

The checker takes a capture width, height, frame rate, frame count, Y′CbCr matrix and range. It reports the actual frame dimensions and strides, sampled pixel count and time spent in the analyzer. A live run requires the TV's existing video backend and exclusive use of capture. The Windows and emulated ARM analyzers both passed 39 comparisons against desktop grid and reduction results on independently decoded test inputs.

## Integration status

Full-frame comparisons still need the desktop region mapping, black-bar handling and Entertainment output. Capture-side HDR encoding on the C9 needs its own check. An installer, measured capture cost, and opt-in boot and standby recovery are pending. PicCap's underlying capture project supplies the C9-generation video backend and frame plane/stride API. [webOS capture backend](https://github.com/webosbrew/hyperion-webos)
