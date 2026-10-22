param([switch]$SkipEsp, [string]$Vdac4='E:\zx\Tang Nano 20k VDAC\VDAC4')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not $env:IDF_PYTHON_ENV_PATH) { . 'E:\zx\esp\idf-env.ps1' }
$python=Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
if (-not $SkipEsp) {
    & $python "$env:IDF_PATH\tools\idf.py" -B build -D IDF_TARGET=esp32s3 -D "VDAC4_ROOT=$Vdac4" reconfigure
    if($LASTEXITCODE) { throw 'ESP configure failed' }
    & cmake --build build --target elf
    if($LASTEXITCODE) { throw 'ESP build failed' }
}
New-Item -ItemType Directory -Force dist/DOOM | Out-Null
Copy-Item build/doom_vdac4.app.elf dist/DOOM/DOOM.ELF -Force
& $python "$Vdac4\ESP32 S3 Zero N8R8\Sound\tools\check_module_symbols.py" dist/DOOM/DOOM.ELF
if($LASTEXITCODE) { throw 'ELF loader compatibility check failed' }
$env:SJASMPLUS='E:\zx\z80\zuma\sjasmplus.exe'
& $python third_party/fat32/build.py
if($LASTEXITCODE) { throw 'FAT32 build failed' }
& $python tools/make_launcher_pic.py
if($LASTEXITCODE) { throw 'Launcher picture failed' }
& $env:SJASMPLUS --nologo --inc=launcher --inc=build --inc=third_party/fat32/include --sym=build/doom_z80.sym --lst=build/doom_z80.lst launcher/doom.asm
if($LASTEXITCODE) { throw 'Z80 build failed' }
& $python tools/package.py
if($LASTEXITCODE) { throw 'SPG packaging failed' }
Write-Host 'Built dist/DOOM/_DOOM.SPG and dist/DOOM/DOOM.ELF'
