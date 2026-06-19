# Builds the Windows Wisp executable with PyInstaller and required assets.
param(
    [switch]$Clean,
    [switch]$SkipInstall,
    # Kept for backward compatibility (CI passes it). Auto-install is now the
    # default, so this switch is accepted but no longer changes behavior.
    [switch]$Yes,
    [switch]$UseGlobalPython
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$PythonVersionFile = Join-Path $Root ".python-version"
$SpecName = "Wisp.spec"
$AppName = "Wisp"
$RequirementsFile = "requirements.txt"
$BuildRequirementsFile = "requirements-build.txt"
$Spec = Join-Path $Root "packaging\$SpecName"
$DistExe = Join-Path $Root "dist\$AppName\$AppName.exe"
$IconPath = Join-Path $Root "assets\app.ico"
$IconSourcePng = Join-Path $Root "assets\doll\idle.png"

Set-Location $Root

$ExpectedPython = ""
if (-not (Test-Path $PythonVersionFile)) {
    throw ".python-version is required and must contain an exact Python version like 3.12.13."
}
$ExpectedPython = (Get-Content $PythonVersionFile -TotalCount 1).Trim()
if (-not $ExpectedPython) {
    throw ".python-version is required and must contain an exact Python version like 3.12.13."
}
if ($ExpectedPython -notmatch '^\d+\.\d+\.\d+$') {
    throw ".python-version must contain an exact Python version like 3.12.13."
}
$ExpectedMinor = ($ExpectedPython -split "\.")[0..1] -join "."

function Require-PackagingFile {
    param(
        [string]$Path,
        [string]$Name
    )

    if ((-not (Test-Path -LiteralPath $Path -PathType Leaf)) -or ((Get-Item -LiteralPath $Path).Length -eq 0)) {
        throw "$Name is required for packaging."
    }
}

function Require-PackagingDirectory {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Name is required for packaging."
    }
    $FirstChild = Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $FirstChild) {
        throw "$Name must contain files for packaging."
    }
}

$RequiredBuildFiles = @(
    @{ Path = (Join-Path $Root $RequirementsFile); Name = $RequirementsFile },
    @{ Path = (Join-Path $Root $BuildRequirementsFile); Name = $BuildRequirementsFile }
)
foreach ($RequiredBuildFile in $RequiredBuildFiles) {
    Require-PackagingFile -Path $RequiredBuildFile.Path -Name $RequiredBuildFile.Name
}

$RequiredPackagingFiles = @(
    @{ Path = $Spec; Name = "packaging\$SpecName" },
    @{ Path = (Join-Path $Root "runtime\supervisor\app.py"); Name = "runtime\supervisor\app.py" },
    @{ Path = (Join-Path $Root ".env.example"); Name = ".env.example" }
)
foreach ($RequiredPackagingFile in $RequiredPackagingFiles) {
    Require-PackagingFile -Path $RequiredPackagingFile.Path -Name $RequiredPackagingFile.Name
}

$RequiredPackagingDirectories = @(
    @{ Path = (Join-Path $Root "assets"); Name = "assets" },
    @{ Path = (Join-Path $Root "ui\locales"); Name = "ui\locales" }
)
foreach ($RequiredPackagingDirectory in $RequiredPackagingDirectories) {
    Require-PackagingDirectory -Path $RequiredPackagingDirectory.Path -Name $RequiredPackagingDirectory.Name
}

if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
    Require-PackagingFile -Path $IconSourcePng -Name "assets\doll\idle.png"
}

# Force child Python processes (pip, PyInstaller) to stream their output line by
# line so progress shows up promptly instead of arriving in buffered chunks.
$env:PYTHONUNBUFFERED = "1"

function Invoke-CheckedPython {
    param(
        [string]$Python,
        [string[]]$CommandArgs,
        [string]$StepName
    )

    $ArgumentList = @($CommandArgs | Where-Object { $_ -ne $null -and $_ -ne "" })
    $Process = Start-Process -FilePath $Python -ArgumentList $ArgumentList -NoNewWindow -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "$StepName failed with exit code $($Process.ExitCode)."
    }
}

function Get-PythonVersion {
    param(
        [string]$Python,
        [string[]]$PythonArgs = @()
    )

    try {
        return (& $Python @PythonArgs -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')" 2>$null).Trim()
    } catch {
        return ""
    }
}

function Assert-PythonVersion {
    param([string]$Python)

    $ActualVersion = Get-PythonVersion -Python $Python
    if ($ActualVersion -ne $ExpectedPython) {
        throw "$Python is Python $ActualVersion, but Wisp packaging is pinned to Python $ExpectedPython. Rebuild .venv with scripts\setup_dev.ps1 or rerun the launcher with Python $ExpectedPython installed."
    }
}

function New-ProjectVenv {
    Write-Host "Project virtual environment not found; creating it at:"
    Write-Host "  $VenvDir"

    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $PyLauncherArg = "-$ExpectedMinor"
        $PyVersion = Get-PythonVersion -Python "py" -PythonArgs @($PyLauncherArg)
        if ($PyVersion -eq $ExpectedPython) {
            & py $PyLauncherArg -m venv $VenvDir
            return
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonVersion = Get-PythonVersion -Python "python"
        if ($PythonVersion -eq $ExpectedPython) {
            & python -m venv $VenvDir
            return
        }
    }

    throw "Could not find Python $ExpectedPython to create the project virtual environment."
}

function Test-LongPathRisk {
    param([string]$BaseDir)

    $KnownLongWheelPath = Join-Path $BaseDir "Lib\site-packages\elevenlabs\pronunciation_dictionaries\rules\types\body_set_rules_on_the_pronunciation_dictionary_v_1_pronunciation_dictionaries_pronunciation_dictionary_id_set_rules_post_rules_item.py"
    return $KnownLongWheelPath.Length -ge 240
}

function New-BuildRequirementsFile {
    param([string]$SourcePath)

    $TempPath = Join-Path $env:TEMP "wisp-build-requirements.txt"
    Get-Content $SourcePath |
        Where-Object { $_ -notmatch '^\s*elevenlabs\b' } |
        Set-Content -Path $TempPath -Encoding ascii
    return $TempPath
}

if (-not $UseGlobalPython) {
    if (-not (Test-Path $VenvPython)) {
        New-ProjectVenv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
            throw "Failed to create project virtual environment at $VenvDir."
        }
    }
    $Python = $VenvPython
    Assert-PythonVersion -Python $Python
} else {
    Write-Host "Using global Python because -UseGlobalPython was provided."
    $Python = "python"
    Assert-PythonVersion -Python $Python
}

if (Test-Path $DistExe) {
    $RunningWisp = Get-Process -Name "Wisp" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $DistExe }
    if ($RunningWisp) {
        $Pids = ($RunningWisp | ForEach-Object { $_.Id }) -join ", "
        throw "Cannot rebuild while the packaged app is running. Close Wisp.exe first. Running process id(s): $Pids"
    }
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $Root "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "dist") -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $SkipInstall) {
    Write-Host "Checking dependencies and installing anything missing into:"
    Write-Host "  $Python"
    Write-Host "(already-satisfied packages are skipped automatically)"

    $BuildRequirements = $RequirementsFile
    $FilteredRequirements = $null
    if ((-not $UseGlobalPython) -and (Test-LongPathRisk $VenvDir)) {
        Write-Host "The project path is long enough to hit Windows path limits while installing ElevenLabs."
        Write-Host "Building without ElevenLabs support in this environment; enable long paths if you need that provider bundled."
        $FilteredRequirements = New-BuildRequirementsFile -SourcePath $RequirementsFile
        $BuildRequirements = $FilteredRequirements
    }

    Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install", "--upgrade", "pip") -StepName "pip upgrade"
    try {
        Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install", "-r", $BuildRequirements, "-r", $BuildRequirementsFile) -StepName "dependency install"
    } finally {
        if ($FilteredRequirements -and (Test-Path $FilteredRequirements)) {
            Remove-Item -LiteralPath $FilteredRequirements -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Host "Skipping dependency install (-SkipInstall)."
}

if (-not (Test-Path $IconPath)) {
    if (-not (Test-Path $IconSourcePng)) {
        throw "Cannot create exe icon because the icon source image is missing: $IconSourcePng"
    }
    Write-Host "Creating exe icon from icon image: $IconPath"
    Invoke-CheckedPython -Python $Python -CommandArgs @(
        "-c",
        "from pathlib import Path; from PIL import Image; src=Path(r'$IconSourcePng'); dst=Path(r'$IconPath'); img=Image.open(src).convert('RGBA'); img.save(dst, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    ) -StepName "icon generation"
}

try {
    Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "PyInstaller", "--version") -StepName "PyInstaller version check"
} catch {
    throw "PyInstaller is not installed in $Python. Run without -SkipInstall, or run: $Python -m pip install -r requirements-build.txt"
}

Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "PyInstaller", "--noconfirm", $Spec) -StepName "PyInstaller build"

# Seed %APPDATA%\Wisp\.env with the repo's .env if the user has no settings yet.
# Settings are stored there so they survive rebuilds and updates.
$UserCfg = Join-Path $env:APPDATA "Wisp"
$EnvTarget = Join-Path $UserCfg ".env"
if (-not (Test-Path $UserCfg)) { New-Item -ItemType Directory -Force $UserCfg | Out-Null }
if (-not (Test-Path $EnvTarget)) {
    $envSrc = Join-Path $Root ".env"
    $envExample = Join-Path $Root ".env.example"
    if (Test-Path $envSrc) {
        Copy-Item $envSrc $EnvTarget
    } elseif (Test-Path $envExample) {
        Copy-Item $envExample $EnvTarget
    } else {
        New-Item -ItemType File $EnvTarget | Out-Null
    }
    Write-Host "Created $EnvTarget (initial settings)"
} else {
    Write-Host "Keeping existing settings at $EnvTarget"
}

Write-Host ""
Write-Host "Built app folder: $Root\dist\$AppName"
Write-Host "Executable:       $Root\dist\$AppName\$AppName.exe"
Write-Host "Settings file:    $EnvTarget  (persists across rebuilds)"
