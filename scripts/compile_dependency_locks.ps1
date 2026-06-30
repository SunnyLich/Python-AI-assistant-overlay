# Regenerates locked requirement files from the shared human-edited manifests.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PythonVersionFile = Join-Path $Root ".python-version"
if ((-not (Test-Path $PythonVersionFile)) -or ((Get-Item $PythonVersionFile).Length -eq 0)) {
    throw ".python-version is required and must contain a Python version like 3.12 or 3.12.13."
}
$Want = (Get-Content $PythonVersionFile -TotalCount 1).Trim()
if ($Want -notmatch '^\d+\.\d+(\.\d+)?$') {
    throw ".python-version must contain a Python version like 3.12 or 3.12.13."
}
$WantMinor = ($Want -split "\.")[0..1] -join "."

function Require-Input {
    param([string]$Path)
    if ((-not (Test-Path -LiteralPath $Path)) -or ((Get-Item -LiteralPath $Path).Length -eq 0)) {
        throw "$Path is required to compile dependency locks."
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required to compile dependency lock files. Install it from https://docs.astral.sh/uv/ and rerun this script."
}

Require-Input "requirements.txt"
Require-Input "requirements-dev.txt"
Require-Input "requirements-build.txt"

function Compile-RuntimeLock {
    param(
        [string]$Platform,
        [string]$OutputFile
    )
    uv pip compile requirements.txt `
        --upgrade `
        --no-header `
        --python-version $WantMinor `
        --python-platform $Platform `
        --output-file $OutputFile
    Write-Host "Updated $OutputFile for $Platform / Python $WantMinor."
}

function Compile-UniversalLock {
    param(
        [string]$InputFile,
        [string]$OutputFile
    )
    uv pip compile $InputFile `
        --upgrade `
        --universal `
        --no-header `
        --python-version $WantMinor `
        --output-file $OutputFile
    Write-Host "Updated $OutputFile for Python $WantMinor."
}

$Targets = $args
if ($Targets.Count -eq 0) {
    $Targets = @("all")
}

foreach ($Target in $Targets) {
    switch ($Target) {
        "all" {
            Compile-RuntimeLock "x86_64-pc-windows-msvc" "requirements-windows.lock"
            Compile-RuntimeLock "x86_64-manylinux_2_34" "requirements-linux.lock"
            Compile-RuntimeLock "aarch64-apple-darwin" "requirements-macos.lock"
            Compile-UniversalLock "requirements-dev.txt" "requirements-dev.lock"
            Compile-UniversalLock "requirements-build.txt" "requirements-build.lock"
        }
        "windows" { Compile-RuntimeLock "x86_64-pc-windows-msvc" "requirements-windows.lock" }
        "linux" { Compile-RuntimeLock "x86_64-manylinux_2_34" "requirements-linux.lock" }
        "macos" { Compile-RuntimeLock "aarch64-apple-darwin" "requirements-macos.lock" }
        "dev" { Compile-UniversalLock "requirements-dev.txt" "requirements-dev.lock" }
        "build" { Compile-UniversalLock "requirements-build.txt" "requirements-build.lock" }
        default { throw "Unknown lock target '$Target'. Use all, windows, linux, macos, dev, or build." }
    }
}
