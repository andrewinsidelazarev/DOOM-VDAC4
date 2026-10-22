param(
    [switch]$LauncherOnly,
    [switch]$ElfOnly,
    [string]$Target='\\192.168.1.113\1\Games\VDAC4\DOOM VDAC4'
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
# The destination must already be the game directory. Never create or infer a
# parent folder: Games\VDAC4 contains other games, not this installation.
$directory=Get-Item -LiteralPath $Target
if (-not $directory.PSIsContainer) {throw 'Deployment target is not a directory'}
if ($LauncherOnly -and $ElfOnly) {throw 'Choose LauncherOnly or ElfOnly'}
$files=if ($LauncherOnly) {@('_DOOM.SPG')} elseif ($ElfOnly) {@('DOOM.ELF')} else {@('_DOOM.SPG','DOOM.ELF','README.TXT','COPYING.TXT','LICENSES.TXT')}
foreach($name in $files) {
    $source=Join-Path "$root\dist\DOOM" $name
    $destination=Join-Path $directory.FullName $name
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $expected=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $actual=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if($expected -ne $actual) {throw "Hash mismatch: $destination"}
    [pscustomobject]@{File=$destination; SHA256=$actual}
}
