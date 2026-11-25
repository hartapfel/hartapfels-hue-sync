param([Parameter(Mandatory)][ValidateSet('Compiler','Sources','Packager')][string]$Stage)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
New-Item -ItemType Directory -Force build/flatc,build/packager | Out-Null
function Download($url, $path, $sha256) {
    Write-Host "Fetching $path"
    curl.exe --fail --silent --show-error --location --connect-timeout 15 --max-time 120 --retry 1 $url --output $path
    if ($sha256 -and (Get-FileHash $path).Hash -ne $sha256) { throw "Unexpected digest for $path" }
}

if ($Stage -eq 'Compiler') {
    python -m pip install --disable-pip-version-check --target build/zig-toolchain ziglang==0.13.0
    return
}
if ($Stage -eq 'Packager') {
    npm install --prefix build/packager --no-audit --no-fund --ignore-scripts @webosose/ares-cli@2.3.1
    return
}
Download 'https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.7/mbedtls-3.6.7.tar.bz2' build/mbedtls.tar.bz2 a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6
tar -xf build/mbedtls.tar.bz2 -C build
$flatRevision = '595bf0007ab1929570c7671f091313c8fc20644e'
Download "https://github.com/google/flatbuffers/archive/$flatRevision.tar.gz" build/flatbuffers.tar.gz
tar -xf build/flatbuffers.tar.gz -C build "flatbuffers-$flatRevision/include" "flatbuffers-$flatRevision/LICENSE"
Download 'https://github.com/google/flatbuffers/releases/download/v24.3.25/Windows.flatc.binary.zip' build/flatc.zip
Expand-Archive build/flatc.zip build/flatc -Force
$picRevision = '00c932f092084b234b3f619b060a98fa134c12bd'
Download "https://codeload.github.com/webosbrew/hyperion-webos/tar.gz/$picRevision" build/piccap.tar.gz
tar -xf build/piccap.tar.gz -C build "hyperion-webos-$picRevision/src" "hyperion-webos-$picRevision/unicapture" "hyperion-webos-$picRevision/fbs"
Rename-Item "build/hyperion-webos-$picRevision" piccap
Download 'https://github.com/TBSniller/piccap/releases/download/0.5.3/org.webosbrew.piccap_0.5.3_all.ipk' build/piccap-0.5.3.ipk
