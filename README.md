# C9 Screen Sync

This standalone webOS project is porting the video path from the locally installed Hue Sync Desktop 1.13.1.83. The target is an LG C9 using local video capture and a Hue Entertainment area. The test IPK includes a TV setup screen, a webOS service and an ARM video capture and streaming worker.

## Desktop comparisons

The reference executable has SHA-256 `2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad`. The test harness reads selected numeric routines from the user's copy into an offline CPU emulator. The tests generate all image inputs locally.

* The CPU bitmap analyzer samples a rectangular grid with a fixed budget. It returns two RGB colors per region, an integer mean and a per-channel trimean. The port accepts strided 8-bit RGBX/BGRX and packed 10-bit RGB. Black/white distributions, random colors, odd/even sample counts, and all three formats matched the reference grid and reduction in 42 cases.
* The video interpolation loop converts a region color to CIELAB, updates lightness and chroma with separate coefficients, and converts back to sRGB. The ordinary filtering and color transforms matched the reference instructions in 750 comparisons on Windows x64. The optical result depends on stages before and after these operations.
* Another 528 comparisons exercise successive updates for one light in Video mode at Moderate intensity. They cover gain, peak limiting, temporal filtering, the black cutoff and the zero-delay output queue, with audio and background lighting disabled. Tests include irregular update intervals from 4.1 to 89 ms. Each output channel matched the desktop calculation within 0.00001 on a scale from zero to one.
* The installed configuration selects CPU sampling. There is a separate OpenCL kernel in the executable. Its sampling math differs from the CPU path.
* The single-screen Video Default preset has 14 overlapping screen rectangles, each associated with a volume in the room. The capture path uses the desktop rectangle rounding and sample allocation. In Video Moderate it accepts a new regional trimean only when both the mean and the trimean change by more than two 8-bit code values in at least one channel each. Capture updates the target independently of the interpolation clock.
* The black-bar detector probes one column and one row per frame. It uses the desktop's seeded sampling sequence and a rolling median of 201 observations to determine a symmetric active picture. A 530-frame sequence with changing letterboxes, pillarboxes, dark frames and irregular boundaries produced the same raw observations and every crop rectangle as the installed desktop detector. The linked ARM capture checker matched all 530 crop rectangles.

The ARM worker compiles four numeric stages from the installed Hue Sync executable at build time. These stages are RGB to Lab, elapsed-time smoothing coefficients, the Lab history update, and Lab to RGB. The generator verifies the desktop executable's digest, reads the fixed instruction blocks and their constants, and emits an intermediate C file in the build directory. The target compiler produces native code with floating-point contraction disabled. The power function comes from the target math library. Capture, regional sampling, gain and black cutoff, playback timing, and bridge transport are implemented separately.

Builds require Python, LLVM's `llvm-objdump`, CMake, a C compiler and the reference Hue Sync Desktop installation. Set `-DHUE_SYNC_DESKTOP=/path/to/HueSync.exe` and `-DLLVM_OBJDUMP=/path/to/llvm-objdump` if CMake cannot find them. On Windows, install `unicorn==2.1.4` for the desktop comparison tests and run the following from this directory.

```powershell
cmake -S . -B build
cmake --build build --config Release
python tests/desktop_bitmap.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll
python tests/desktop_color.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll
```

## C9 video capture

The capture adapter loads the DILE video backend used by PicCap 0.5.3 and borrows each frame until analysis finishes. It supports RGB24, NV12 and NV16. Only pixels on the sampling grid undergo Y′CbCr decoding. Each region retains its position in the original chroma planes, including regions that start on an odd row or column. The matrix and video range are explicit inputs. Primaries and transfer-function conversion for HDR are still pending. [PicCap 0.5.3](https://github.com/TBSniller/piccap/releases/tag/0.5.3)

In the reference executable, the Windows desktop duplication call at RVA `0x1b8d19` requests five DXGI formats (24, 28, 29, 87 and 91). They represent packed 10-bit RGB and four variants of 8-bit RGBA or BGRA. DXGI can convert a fullscreen surface before capture. The list establishes which pixel layouts the desktop app accepts, but it does not establish the transfer function of a C9 capture. [Microsoft DXGI duplication API](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_5/nf-dxgi1_5-idxgioutput5-duplicateoutput1) · [DXGI format definitions](https://learn.microsoft.com/en-us/windows/win32/api/dxgiformat/ne-dxgiformat-dxgi_format)

PicCap's TCP image messages carry dimensions, pixel data and optional plane strides. PicCap reports the TV's HDR mode separately to HyperHDR's JSON API. To determine how the C9 capture represents HDR, compare captured code values from known HDR patches with the source values before choosing a conversion. [PicCap image schema](https://github.com/webosbrew/hyperion-webos/blob/00c932f092084b234b3f619b060a98fa134c12bd/fbs/hyperion_request.fbs) · [PicCap HDR reporting](https://github.com/webosbrew/hyperion-webos/blob/00c932f092084b234b3f619b060a98fa134c12bd/src/json_rpc_client.c)

### Measuring an HDR10 capture on the C9

Generate the 15-second HEVC Main 10 chart with `python tests/hdr_chart.py ffmpeg build/c9-hdr10-chart.mp4` and an FFmpeg build with 10-bit libx265. It contains a marker row, twelve gray patches from black through 1,000 cd/m², and six BT.2020 color patches. The generator decodes the file and checks all patch centers at three timestamps. The PQ reference and narrow-range encoding follow [ITU-R BT.2100](https://www.itu.int/rec/R-REC-BT.2100).

1. Stop standalone sync. For direct capture, pause any other app using the capture device. For PicCap input, point PicCap at TCP `127.0.0.1:19401`. Play the chart full-screen on the input under investigation and check the C9 indicates HDR playback.
2. In a Dev Manager TV terminal, change into the installed `org.webosbrew.c9screensync.service` directory. Run `./c9-sync --measure-hdr-chart direct` for direct capture, or `./c9-sync --measure-hdr-chart` for PicCap. Both modes work without a Hue connection. Loop the clip if needed.
3. Save its single JSON output line on the PC and run `python tests/read_hdr_chart.py measurement.json`. It lists raw gray values and reports deviations from unmodified full- and limited-range PQ for the gray and color patches. Agreement supports that interpretation for that playback path. A large residual requires further investigation of capture or playback processing.

The worker samples five interior points per tile, requires the marker and eight stable frames, and reports raw RGB or Y/Cb/Cr averages before matrix conversion or sync grading. The local test decodes the generated clip, sends RGB24 and padded NV12 through the PicCap protocol, interrupts a run of seven chart frames with a blank frame, and checks all 24 triples for both formats.

To compare against a Windows HDR capture, build the chart reader from this directory in an x64 Visual Studio Developer PowerShell. Its self-test checks channel order and readback using offscreen D3D11 textures.

```powershell
New-Item -ItemType Directory -Force build/chart-tools | Out-Null
cl /nologo /std:c++17 /EHsc /W4 /O2 tests/windows_hdr_chart.cpp /Fo:build/chart-tools/windows_hdr_chart.obj /Fe:build/chart-tools/c9-desktop-chart.exe dxgi.lib d3d11.lib user32.lib
& ./build/chart-tools/c9-desktop-chart.exe --self-test
```

On a PC with an active HDR10 output, the following measurement briefly displays a small HDR10 window on that monitor. It uses the desktop app's duplication API and format list. It reads the 24 patch centers after three stable presentations. The result describes the Windows compositor on that monitor. The `dxgiFormat` field identifies the returned pixels, while `bitsPerColor` describes the display output. [Microsoft desktop duplication](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_5/nf-dxgi1_5-idxgioutput5-duplicateoutput1) · [Microsoft output properties](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_6/ns-dxgi1_6-dxgi_output_desc1)

```powershell
$reference = & ./build/chart-tools/c9-desktop-chart.exe --measure
if ($LASTEXITCODE -eq 0) {
    $reference | Set-Content -Encoding utf8 build/desktop-hdr-reference.json
    python tests/read_hdr_chart.py measurement.json --desktop build/desktop-hdr-reference.json
}
```

The comparison reports gray and color errors separately on a 0–255 RGB scale. For NV12 and NV16 it tries BT.601, BT.709 and BT.2020 at both video and full range. For RGB24 it tries both ranges. If all decoders show large gray errors, changing the Y′CbCr matrix alone cannot explain that brightness difference.

With Zig 0.13.0 and a checkout of `webosbrew/hyperion-webos` at `00c932f092084b234b3f619b060a98fa134c12bd`, the following PowerShell commands cross-compile the checker locally and test the linked ARM code. Set `$piccap` to that checkout.

```powershell
cmake -S . -B build/arm -G Ninja -DCMAKE_BUILD_TYPE=Release --toolchain cmake/c9-zig.cmake "-DPICCAP_SOURCE_DIR=$piccap"
cmake --build build/arm --target c9-capture-check
python tests/capture_bitmap.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll build/arm/c9-capture-check
python tests/desktop_video.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll "$env:APPDATA/HueSync/config.json" build/arm/c9-capture-check
python tests/desktop_bars.py 'C:/Program Files/Hue Sync/HueSync.exe' build/Release/c9_bitmap.dll build/arm/c9-capture-check
```

The checker takes a capture width, height, frame rate, frame count, Y′CbCr matrix and range. It reports frame dimensions and strides, sample count, processing time across all 14 regions, and their final RGB targets. For a live run, place the capture checker beside the packaged `libdile_vt_backend.so` and release any competing capture session. Windows and emulated ARM both passed 39 capture-plane comparisons, 1,296 frame-to-target cases, and 343 room-position checks against the desktop EDK's cuboid membership routine. Boundary points can belong to multiple cuboids.

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

With Zig 0.13, Ninja, llvm-ar and llvm-ranlib on PATH, use the same sources to build for the TV. Set `$piccap` to the absolute path of the capture source checkout described above. The worker builds its request verifier and reply encoder from that checkout's schemas. Set `$flatc` to the host FlatBuffers 24.3.25 compiler and `$flatbuffers` to the matching source directory. [FlatBuffers source](https://github.com/google/flatbuffers/tree/595bf0007ab1929570c7671f091313c8fc20644e)

```powershell
cmake -S . -B build/arm -G Ninja -DCMAKE_BUILD_TYPE=Release --toolchain cmake/c9-zig.cmake "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" "-DMBEDTLS_SOURCE_DIR=$tls" "-DPICCAP_SOURCE_DIR=$piccap" "-DFLATC_EXECUTABLE=$flatc" "-DFLATBUFFERS_INCLUDE_DIR=$flatbuffers/include"
cmake --build build/arm --target c9-sync c9-capture-check
```

The mixer composites the rendered colors of all matching regions in effect-layer order. For 272 cases including shared volumes, transparent layers and opaque layers, the host and linked ARM code matched the desktop mixer's three double-precision results exactly. The test supplies the rendered layers, so it can isolate composition from capture and the creation of each effect.

The ordinary video area effect preserves its input RGB and gives it full opacity inside the room volume, including on its boundary. Outside the volume the effect returns transparent black. Running the installed desktop constructor, color setter and renderer on 225 colors at three positions confirmed all 675 results through the mixer and RGB16 serializer. This check covers the default video effect with no optional brightness override. Run it with `python tests/desktop_area.py 'C:/Program Files/Hue Sync/HueSync.exe'`.

```powershell
cmake --build build/host-msvc --target c9_bitmap
python tests/desktop_mixer.py 'C:/Program Files/Hue Sync/HueSync.exe' build/host-msvc/c9_bitmap.dll build/arm/c9-capture-check
```

## Integration status

The ARM worker `c9-sync` connects DILE frame capture, regional analysis, Video/Moderate interpolation, room-position mixing and the DTLS stream. The service sends the selected area, channels and pairing credential through the child's private stdin. It reports streaming after the first successful send and checks area ownership before release on cancellation or failure. Keep `libc9_hue_stream.so` beside the executable.

The installed desktop configuration specifies 60 interpolation updates per second and zero sync delay. On its first update the desktop uses that nominal rate. On subsequent updates it calculates smoothing from the actual elapsed time. The worker does the same, sharing the lightness and chroma coefficients across zones for each tick. The local integration fixture feeds a 10 Hz red/blue split screen, introduces an 85 ms stall, and compares the decrypted RGB codes of both channels with the interpolator driven by recorded timestamps. The worker targets 50 Hue packets per second, the public EDK streaming default. Desktop packet timing and total capture-to-light latency on the physical setup still require measurement.

```powershell
cmake -S . -B build/host-msvc -DC9_WORKER_TEST=ON "-DPICCAP_SOURCE_DIR=$piccap" "-DFLATC_EXECUTABLE=$flatc" "-DFLATBUFFERS_INCLUDE_DIR=$flatbuffers/include"
cmake --build build/host-msvc --target c9-sync-fixture c9_bitmap
python tests/worker_loopback.py build/host-msvc/c9-sync-fixture.exe build/host-msvc/c9_bitmap.dll (Resolve-Path build/node-v0.12.2.exe).Path 'C:/Program Files/Git/usr/bin/openssl.exe'
python tests/piccap_loopback.py build/host-msvc/c9-sync-fixture.exe build/host-msvc/c9_bitmap.dll (Resolve-Path build/node-v0.12.2.exe).Path 'C:/Program Files/Git/usr/bin/openssl.exe' $flatc "$piccap/fbs"
node tests/session.js
```

### Running the packaged ARM worker

`tests/arm_package.py` extracts the worker, capture backend, streaming library and session encoder from the IPK. In an isolated ARMv7 QEMU guest, the worker locates the real backend and reports its missing LG firmware dependency. A separate test backend supplies padded NV16 planes through the native capture ABI. The worker acquires and releases eight frames and measures all 24 chart patches. The guest also feeds PicCap NV12 and RGB frames, decrypts the resulting two-channel Hue stream, and checks channel IDs, sequence numbers and the first red/blue records against the desktop renderer and serializer.

The fixture uses these files in `build/arm-vm`:

* `vmlinuz`, `alpine.tar.gz` and `openssl.apk` come from the Alpine 3.22 ARMv7 netboot LTS kernel, minirootfs 3.22.8 and OpenSSL 3.5.9-r0 respectively. [Alpine ARMv7 releases](https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/armv7/) · [Alpine ARMv7 packages](https://dl-cdn.alpinelinux.org/alpine/v3.22/main/armv7/)
* `sdk-2026.tar.gz` is the 2026.08-webos aarch64-host SDK archive containing the arm-webos-linux-gnueabi target sysroot. Its loader checks the package's library and symbol-version dependencies. [OpenLGTV SDK release](https://github.com/openlgtv/buildroot-nc4/releases/tag/2026.08-webos)
* Supply a Debian armel libc6 package as the final argument for the execution test. Both 2.13-38+deb7u10 and 2.24-11+deb9u4 passed the chart and DTLS tests. When the guest used the SDK 2.12.2 loader and libraries for execution, the SDK's own `getconf GNU_LIBC_VERSION`, a minimal C program and the packaged worker all exited with signal 11. Loader diagnostics placed the worker and minimal program failures in libc initialization. The physical C9 startup check is described under TV test package. [Debian eglibc archive](https://archive.debian.org/debian/pool/main/e/eglibc/) · [Debian glibc archive](https://archive.debian.org/debian/pool/main/g/glibc/)

```powershell
cmake --build build/arm --target c9-test-capture-backend
python tests/desktop_color.py 'C:/Program Files/Hue Sync/HueSync.exe' build/host-msvc/c9_bitmap.dll --arm-vectors build/arm-vm/desktop-ticks.bin --chart-reference "$env:APPDATA/HueSync/config.json" build/arm-vm/desktop-chart.json
python tests/arm_package.py build/ipk/org.webosbrew.c9screensync_0.4.2_all.ipk build/arm-vm $qemu $flatc "$piccap/fbs/hyperion_request.fbs" $armelLibcDeb --desktop-vectors build/arm-vm/desktop-ticks.bin --chart-reference build/arm-vm/desktop-chart.json
```

The optional desktop vectors exercise 528 Video/Moderate ticks inside the worker extracted from the IPK. Each run carries its own history through multiple colors, including black, mixed tones and highlights. On the two ARM runtimes the maximum absolute channel error against the desktop was 0.00000011921 on a 0–1 scale.

The optional chart test places one virtual light at the center of each of the 14 desktop video regions. The ARM worker receives the 24-patch chart through PicCap's NV12 protocol. The reference supplies the desktop analyzer with the corresponding pixels decoded using the worker's configured BT.709 limited range. It runs the desktop sampling, Video/Moderate filter, renderer and serializer. The test compares the actual RGB16 commands at the local DTLS receiver on the first packet and after holding the chart for 16 seconds. This isolates differences introduced after capture. Measuring how C9 firmware and Windows each represent the same HDR scene requires the separate capture measurements above.

The service's bridge client identifies a bridge by its certificate and bridge ID, pairs via its link button, and reads the selected Entertainment area's channel IDs and three-dimensional positions. It checks the active application before requesting start or stop. Pairing and ownership transitions are covered by the local fixture, `node tests/bridge.js`, which also runs on Node 0.12.2. A read-only check on a BSB003 bridge imported all 12 channels of the selected area. Each position matched a single desktop preset cuboid. The certificate check also passed on Windows with the C9-generation Node runtime. The public roots come from the pair documented by OpenHue. [OpenHue bridge roots](https://github.com/openhue/openhue-go/blob/main/certs.go) · [LG service runtime versions](https://webostv.developer.lge.com/develop/guides/js-service-basics)

## TV test package

The C9 Screen Sync app has its own webOS package ID, private pairing storage and background service. Enter the bridge IP, press the bridge link button, pair, then choose an existing Entertainment area. Release HyperHDR's Hue stream before choosing Start sync. Closing the setup screen leaves the session running. Reopen the app to stop it.

**Direct TV capture** is the default for a fresh setup. The service loads its own DILE backend and requests 256 × 144 at 30 fps. It needs access to the firmware capture device. Pause any competing capture session before starting. Existing installations keep their saved input.

For **PicCap stream**, use TCP, address `127.0.0.1`, port `19401`, and leave PicCap capture running. To switch back to HyperHDR, restore PicCap's prior destination. The receiver listens only on the TV loopback interface while starting or syncing and accepts PicCap's RGB24 and NV12 frames, including padded Y and UV strides.

Download the official PicCap 0.5.3 IPK to `build/piccap-0.5.3.ipk` or pass its path with `--piccap-ipk`. The packager extracts only the MIT-licensed capture backend, verifies its digest and includes its copyright notice. After compiling the ARM worker, run the following with the official ares CLI entry point. [PicCap 0.5.3](https://github.com/TBSniller/piccap/releases/tag/0.5.3)

```powershell
python package.py --ares-js /path/to/ares-cli/bin/ares-package.js
```

The packaging script checks the app and service manifests, ARM payloads, executable mode and release metadata before saving the IPK in `build/ipk`. Its inputs include Mbed TLS and the Zig runtime licenses. The TV setup screen and service use LG's JS service packaging model. [LG JS service guide](https://webostv.developer.lge.com/develop/guides/js-service-usage)

Version 0.4.2 was installed on a physical LG C9 through webOS Dev Manager. From the TV's developer shell, the packaged ARM worker started, read empty input and returned its expected invalid-session diagnostic and exit code 2. The packaged JS service answered a public-bus status request from the shell with its expected caller check. These checks verify loading and dispatch on C9 firmware. They end before screen capture, bridge activation or power-on registration.

**Start sync on power-on** is off by default. Enabling it registers a persistent webOS activity after checking access to the TV's power state. After cold boot or Quick Start wake, the service reads the selected layout and waits for the worker to process its first video frame. Only then does it request ownership and allow the DTLS handshake. If the network or video input is slow to appear, it retries with increasing delays up to 30 seconds between attempts. Choose **Stop sync** to cancel a pending wake retry. Standby cancels retries and releases the current session. If a different app owns the area, this wake attempt ends. [LG Activity Manager](https://webostv.developer.lge.com/develop/references/activity-manager)

If PicCap disconnects or fresh frames stop arriving after a power-on stream starts, the app releases its Hue area and retries capture while the TV is awake. Manual Stop and standby cancel recovery. Tests replay video loss during streaming, reconnecting the power subscription, delayed bridge and input availability, and cancellation during bridge activation. The ARM guest starts its test bridge only after the worker reports its first frame. A real cold boot and remote-control wake are required to confirm which power callbacks the C9 firmware delivers.

The desktop comparisons cover the ordinary video effect and mixer. A comparison from a real HDR source through to the physical bridge is still pending. Live pairing with a dedicated app identity, capture cost, cold boot and standby recovery also require device tests. PicCap's underlying capture project supplies the C9-generation video backend and frame plane/stride API. [webOS capture backend](https://github.com/webosbrew/hyperion-webos)
