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

The stream client sends RGB16 updates for the selected entertainment configuration over DTLS 1.2 using its paired application identity and client key. The caller activates the area before opening the stream and supplies each channel's bridge-assigned ID. The loopback test uses temporary credentials and an independent OpenSSL peer. It checks the decrypted payload of 258 frames, including channel order, component byte order and sequence wraparound.

Unpack the official `mbedtls-3.6.7.tar.bz2` source archive in `build/`. Its SHA-256 is `a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6`. [Mbed TLS release](https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.7)

Run the following in a Visual Studio x64 developer shell. The test command uses the MSYS OpenSSL executable from Git for Windows.

```powershell
$tls = (Resolve-Path build/mbedtls-3.6.7).Path
cmake -S . -B build/host-msvc -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" "-DMBEDTLS_SOURCE_DIR=$tls"
cmake --build build/host-msvc --target c9_hue_stream
python tests/stream_loopback.py build/host-msvc/c9_hue_stream.dll 'C:/Program Files/Git/usr/bin/openssl.exe'
```

With Zig 0.13, Ninja, llvm-ar and llvm-ranlib on PATH, use the same sources to build for the TV. Set `$piccap` to the absolute path of the capture source checkout described above.

```powershell
cmake -S . -B build/arm -G Ninja -DCMAKE_BUILD_TYPE=Release --toolchain cmake/c9-zig.cmake "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" "-DMBEDTLS_SOURCE_DIR=$tls" "-DPICCAP_SOURCE_DIR=$piccap"
cmake --build build/arm --target c9_hue_stream c9-capture-check
```

## Integration status

End-to-end comparisons still need live bridge pairing and channel positions, composition where multiple areas affect a channel, black-bar handling, and final desktop RGB quantization. The encrypted stream has been tested on localhost and cross-compiled for the C9. Live bridge output, capture-side HDR encoding, an installer, measured capture cost, and opt-in boot and standby recovery are pending. PicCap's underlying capture project supplies the C9-generation video backend and frame plane/stride API. [webOS capture backend](https://github.com/webosbrew/hyperion-webos)
