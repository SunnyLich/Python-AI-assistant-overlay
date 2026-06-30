# Windows developer setup script for creating the pinned Wisp virtual environment.
param(
    [switch]$UseGlobalPython
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Want = ""
$PythonVersionFile = Join-Path $Root ".python-version"
if (-not (Test-Path $PythonVersionFile)) {
    throw ".python-version is required and must contain a Python version like 3.12 or 3.12.13."
}
$Want = (Get-Content $PythonVersionFile -TotalCount 1).Trim()
if (-not $Want) {
    throw ".python-version is required and must contain a Python version like 3.12 or 3.12.13."
}
if ($Want -notmatch '^\d+\.\d+(\.\d+)?$') {
    throw ".python-version must contain a Python version like 3.12 or 3.12.13."
}
$WantMinor = ($Want -split "\.")[0..1] -join "."
$VenvDir = Join-Path $Root ".venv"
$VenvBackupDir = Join-Path $Root ".venv.rebuild-backup"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsFile = "requirements-windows.lock"
$DevRequirementsFile = "requirements-dev.lock"
$RequiredDependencyFiles = @(
    (Join-Path $Root $RequirementsFile),
    (Join-Path $Root $DevRequirementsFile)
)
foreach ($RequiredDependencyFile in $RequiredDependencyFiles) {
    if ((-not (Test-Path $RequiredDependencyFile)) -or ((Get-Item $RequiredDependencyFile).Length -eq 0)) {
        $Name = Split-Path -Leaf $RequiredDependencyFile
        throw "$Name is required for developer setup."
    }
}

function Get-PythonVersion {
    param([string]$Python)
    try {
        return (& $Python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')" 2>$null).Trim()
    } catch {
        return ""
    }
}

function Test-PythonMatches {
    param([string]$Python)
    $ActualVersion = Get-PythonVersion $Python
    return (Test-Path $Python) -and (($ActualVersion -eq $Want) -or (($Want -match '^\d+\.\d+$') -and $ActualVersion.StartsWith("$Want.")))
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Find-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        return "uv"
    }

    $Candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Ensure-Uv {
    $Uv = Find-Uv
    if ($null -ne $Uv) {
        return $Uv
    }

    Write-Host "Installing uv to provision Python $Want..."
    Invoke-Native "uv installer" "powershell" @(
        "-ExecutionPolicy",
        "ByPass",
        "-NoProfile",
        "-c",
        "irm https://astral.sh/uv/install.ps1 | iex"
    )
    return Find-Uv
}

function Move-VenvForRebuild {
    if (-not (Test-Path -LiteralPath $VenvDir)) {
        return $false
    }
    if (Test-Path -LiteralPath $VenvBackupDir) {
        throw ".venv.rebuild-backup already exists. Remove it after confirming no setup is in progress, then rerun this script."
    }

    Move-Item -LiteralPath $VenvDir -Destination $VenvBackupDir
    return $true
}

function Restore-VenvBackup {
    if (-not (Test-Path -LiteralPath $VenvBackupDir)) {
        return
    }

    Write-Host "Restoring previous .venv after setup failure..."
    if (Test-Path -LiteralPath $VenvDir) {
        Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Move-Item -LiteralPath $VenvBackupDir -Destination $VenvDir
}

function Remove-VenvBackup {
    if (Test-Path -LiteralPath $VenvBackupDir) {
        Remove-Item -LiteralPath $VenvBackupDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Set-Location $Root

$MovedVenvBackup = $false
try {
    if (-not $UseGlobalPython) {
        $RebuildVenv = $false
        if ((Test-Path $VenvPython) -and -not (Test-PythonMatches $VenvPython)) {
            Write-Host "Existing .venv is not Python $Want; rebuilding it for development..."
            $RebuildVenv = $true
        }

        if ((-not (Test-Path $VenvPython)) -or $RebuildVenv) {
            $PyExe = $null
            $PyArgs = @()
            if (Get-Command py.exe -ErrorAction SilentlyContinue) {
                try {
                    & py.exe "-$WantMinor" (Join-Path $Root "scripts\check_python_version.py") $Want *> $null
                    if ($LASTEXITCODE -eq 0) {
                        $PyExe = "py.exe"
                        $PyArgs = @("-$WantMinor")
                    }
                } catch {}
            }
            if ($null -eq $PyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
                $PythonVersion = Get-PythonVersion "python"
                if (($PythonVersion -eq $Want) -or (($Want -match '^\d+\.\d+$') -and $PythonVersion.StartsWith("$Want."))) {
                    $PyExe = "python"
                    $PyArgs = @()
                }
            }
            if ($null -ne $PyExe) {
                if ($RebuildVenv) {
                    $MovedVenvBackup = Move-VenvForRebuild
                }
                Write-Host "Creating development environment at $VenvDir..."
                Invoke-Native "virtual environment creation" $PyExe ($PyArgs + @("-m", "venv", $VenvDir))
            } else {
                Write-Host "No local Python $Want found; using uv to provision it..."
                $Uv = Ensure-Uv
                if ($null -eq $Uv) {
                    throw "Could not find or install uv. Install Python $Want or uv manually, then rerun this script."
                }
                if ($RebuildVenv) {
                    $MovedVenvBackup = Move-VenvForRebuild
                }
                Invoke-Native "uv virtual environment creation" $Uv @("venv", "--python", $Want, $VenvDir)
            }
        }
        $Python = $VenvPython
    } else {
        $Python = "python"
    }

    $ActualVersion = Get-PythonVersion $Python
    if (($ActualVersion -ne $Want) -and (-not (($Want -match '^\d+\.\d+$') -and $ActualVersion.StartsWith("$Want.")))) {
        throw "Development setup requires Python $Want, but $Python is $ActualVersion."
    }

    Invoke-Native "pip upgrade" $Python @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Native "dependency install" $Python @("-m", "pip", "install", "-r", $RequirementsFile, "-r", $DevRequirementsFile)
    Invoke-Native "developer environment preflight" $Python @("scripts\check_dev_environment.py")
    Remove-VenvBackup
} catch {
    if ($MovedVenvBackup) {
        Restore-VenvBackup
    }
    throw
}

Write-Host ""
Write-Host "Developer environment ready."
Write-Host "Run checks with:"
Write-Host "  $Python -m pytest"
Write-Host "  $Python -m ruff check ."
Write-Host "  $Python -m mypy ."
