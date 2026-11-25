$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$dir = (New-Item -ItemType Directory -Force build/reference).FullName
Set-Location $dir
$installer = 'https://firmware.meethue.com/storage/huesyncwin/83/1c8f080a-e057-4dc8-b34a-c681fbd9df3e/HueSyncInstaller_1.13.1.83.exe'
curl.exe -fsSL --retry 3 --max-time 180 $installer -o installer.exe
if ((Get-FileHash installer.exe).Hash -ne 'dbc70e4c2d606e02daf3670286fae520491809cf9a4a6424537116c61d92337c') { throw 'Unexpected installer' }
$unpacker = 'https://github.com/pawstas80/Unpacker/releases/download/v1.1.1/Unpacker-v1.1.1-win-x64.zip'
curl.exe -fsSL --retry 3 --max-time 90 $unpacker -o unpacker.zip
if ((Get-FileHash unpacker.zip).Hash -ne '9209041f081a5fe9740fc18783e4cfb6e21bfffda84bdfdac222c50002de1ece') { throw 'Unexpected extractor' }
Expand-Archive unpacker.zip unpacker
$exe = Get-ChildItem unpacker -Recurse -File -Filter Unpacker.exe
& $exe.FullName installer.exe contents
7z e 'contents/Hue Sync.msi' Data1.cab -y
# Windows Installer's internal file ID for HueSync.exe in this pinned cabinet.
$file = '_77BE444A6E1225BBDF535C1F7CE883C4'
7z e Data1.cab $file -y
if ((Get-FileHash $file).Hash -ne '2b5c0cf7f94bb4b65fce2766586f22cf0dc0023781511945716af8474362b7ad') { throw 'Unexpected desktop reference' }
Rename-Item $file HueSync.exe
"HUE_REFERENCE=$dir/HueSync.exe" >> $env:GITHUB_ENV
