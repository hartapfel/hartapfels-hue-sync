# C9 Screen Sync

This standalone webOS project is porting the video path from the locally installed Hue Sync Desktop 1.13.1.83. The target is an LG C9 using local video capture and a Hue Entertainment area. The current build produces processing and Hue streaming libraries and an ARM video capture checker.

## Desktop comparisons

The reference executable has SHA-256 `2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad`. The test harness reads selected numeric routines from the user's copy into an offline CPU emulator. The tests generate all image inputs locally.

* The CPU bitmap analyzer samples a rectangular grid with a fixed budget. It returns two RGB colors per region, an integer mean and a per-channel trimean. The port accepts strided 8-bit RGBX/BGRX and packed 10-bit RGB. Black/white distributions, random colors, odd/even sample counts, and all three formats matched the reference grid and reduction in 42 cases.
* The video interpolation loop converts a region color to CIELAB, updates lightness and chroma with separate coefficients, and converts back to sRGB. The ordinary filtering and color transforms matched the reference instructions in 750 comparisons on Windows x64. The optical result depends on stages before and after these operations.
* Another 352 comparisons exercise successive frames for one light in Video mode at Moderate intensity. They cover gain, peak limiting, temporal filtering and the output black cutoff, with audio and background lighting disabled. Each output channel matched the desktop calculation within 0.00001 on a scale from zero to one.
* The installed configuration selects CPU sampling. There is a separate OpenCL kernel in the executable. Its sampling math differs from the CPU path.
* The single-screen Video Default preset has 14 overlapping screen rectangles, each associated with a volume in the room. The capture path uses the desktop rectangle rounding and sample allocation. In Video Moderate it accepts a new regional trimean only when both the mean and the trimean change by more than two 8-bit code values in at least one channel each. Capture updates the target independently of the interpolation clock.

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
zig cc -target arm-linux-gnueabi.2.17 -mcpu=cortex_a9 -O2 -ffp-contract=off '-Wl,--export-dynamic' "-I$piccap/unicapture" "-I$piccap/src" src/capture.c src/capture_check.c src/bitmap.c src/color.c src/video.c -ldl -lm -o build/c9-capture-check
python tests/capture_bitmap.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll build/c9-capture-check
python tests/desktop_video.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll "$env:APPDATA/HueSync/config.json" build/c9-capture-check
```

The checker takes a capture width, height, frame rate, frame count, Y′CbCr matrix and range. It reports frame dimensions and strides, sample count, processing time across all 14 regions, and their final RGB targets. A live run requires the TV's existing video backend and exclusive use of capture. Windows and emulated ARM both passed 39 capture-plane comparisons, 1,296 frame-to-target cases, and 343 room-position checks against the desktop EDK's cuboid membership routine. Boundary points can belong to multiple cuboids.

## Hue Entertainment stream

The stream client accepts normalized RGB colors and the bridge-assigned channel IDs. It clamps each component, scales by 65,535 in double precision and truncates it to a 16-bit code. The caller activates the entertainment configuration before opening the DTLS 1.2 stream. A local OpenSSL peer decrypted 258 frames. All 516 channel records byte-matched the RGB record path in the installed desktop executable, including inputs immediately above and below code boundaries. Sequence wrap and byte order were also checked.

Unpack the official `mbedtls-3.6.7.tar.bz2` source archive in `build/`. Its SHA-256 is `a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6`. [Mbed TLS release](https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.7)

Run the following in a Visual Studio x64 developer shell. The test command uses the MSYS OpenSSL executable from Git for Windows.

```powershell
$tls = (Resolve-Path build/mbedtls-3.6.7).Path
cmake -S . -B build/host-msvc -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" "-DMBEDTLS_SOURCE_DIR=$tls"
cmake --build build/host-msvc --target c9_hue_stream
python tests/stream_loopback.py build/host-msvc/c9_hue_stream.dll 'C:/Program Files/Git/usr/bin/openssl.exe' 'C:/Program Files/Hue Sync/HueSync.exe'
```

With Zig 0.13, Ninja, llvm-ar and llvm-ranlib on PATH, use the same sources to build for the TV. Set `$piccap` to the absolute path of the capture source checkout described above.

```powershell
cmake -S . -B build/arm -G Ninja -DCMAKE_BUILD_TYPE=Release --toolchain cmake/c9-zig.cmake "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" "-DMBEDTLS_SOURCE_DIR=$tls" "-DPICCAP_SOURCE_DIR=$piccap"
cmake --build build/arm --target c9-sync c9-capture-check
```

The mixer composites the rendered colors of all matching regions in effect-layer order. For 272 cases including shared volumes, transparent layers and opaque layers, the host and linked ARM code matched the desktop mixer's three double-precision results exactly. The test supplies the rendered layers, so it can isolate composition from capture and the creation of each effect.

```powershell
cmake --build build/host-msvc --target c9_bitmap
python tests/desktop_mixer.py 'C:/Program Files/Hue Sync/HueSync.exe' build/host-msvc/c9_bitmap.dll build/arm/c9-capture-check
```

## Integration status

The ARM worker `c9-sync` connects DILE frame capture, regional analysis, Video/Moderate interpolation, room-position mixing and the DTLS stream. The service sends the selected area, channels and pairing credential through the child's private stdin. It reports streaming after the first successful send and checks area ownership before release on cancellation or failure. Keep `libc9_hue_stream.so` beside the executable.

The local integration fixture replaces the TV frame producer with a 10 Hz red/blue split screen. Its OpenSSL peer decrypts the worker's packets and checks both channel IDs and every RGB code against the tested Video/Moderate interpolator. The worker schedules interpolation at 60 Hz and sends at 50 Hz. The latter is the public EDK streaming default. Desktop timing with the physical bridge still requires measurement.

```powershell
cmake -S . -B build/host-msvc -DC9_WORKER_TEST=ON
cmake --build build/host-msvc --target c9-sync-fixture c9_bitmap
python tests/worker_loopback.py build/host-msvc/c9-sync-fixture.exe build/host-msvc/c9_bitmap.dll (Resolve-Path build/node-v0.12.2.exe).Path 'C:/Program Files/Git/usr/bin/openssl.exe'
node tests/session.js
```

The service's bridge client identifies a bridge by its certificate and bridge ID, pairs via its link button, and reads the selected Entertainment area's channel IDs and three-dimensional positions. It checks the active application before requesting start or stop. Pairing and ownership transitions are covered by the local fixture, `node tests/bridge.js`, which also runs on Node 0.12.2. A read-only check on a BSB003 bridge imported all 12 channels of the selected area. Each position matched a single desktop preset cuboid. The certificate check also passed on Windows with the C9-generation Node runtime. The public roots come from the pair documented by OpenHue. [OpenHue bridge roots](https://github.com/openhue/openhue-go/blob/main/certs.go) · [LG service runtime versions](https://webostv.developer.lge.com/develop/guides/js-service-basics)

## TV test package

The C9 Screen Sync app has its own webOS package ID, private pairing storage and background service. Enter the bridge IP, press the bridge link button, pair, then choose an existing Entertainment area. Start sync requests the area and opens the installed PicCap video backend. Pause PicCap capture and release HyperHDR's Hue stream before this step. Closing the setup screen leaves an active sync session running. Reopen the app to stop it.

After compiling the ARM worker, run the following from this repository with the official ares CLI entry point:

```powershell
python package.py --ares-js /path/to/ares-cli/bin/ares-package.js
```

The packaging script checks the app and service manifests, ARM payloads, executable mode and release metadata before saving the IPK in `build/ipk`. Its inputs include Mbed TLS and the Zig runtime licenses. The TV setup screen and service use LG's JS service packaging model. [LG JS service guide](https://webostv.developer.lge.com/develop/guides/js-service-usage)

End-to-end comparisons still need the effect inputs to the mixer, black-bar handling and HDR capture behavior. Live pairing with a dedicated app identity, live bridge output, measured capture cost and boot and standby recovery still require device tests. PicCap's underlying capture project supplies the C9-generation video backend and frame plane/stride API. [webOS capture backend](https://github.com/webosbrew/hyperion-webos)
