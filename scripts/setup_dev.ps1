param(
    [switch]$UseGlobalPython
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Want = "3.12.13"
$PythonVersionFile = Join-Path $Root ".python-version"
if (Test-Path $PythonVersionFile) {
    $Want = (Get-Content $PythonVersionFile -TotalCount 1).Trim()
}
$WantMinor = ($Want -split "\.")[0..1] -join "."
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Get-PythonMinor {
    param([string]$Python)
    try {
        return (& $Python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null).Trim()
    } catch {
        return ""
    }
}

function Test-PythonMatches {
    param([string]$Python)
    return (Test-Path $Python) -and ((Get-PythonMinor $Python) -eq $WantMinor)
}

Set-Location $Root

if (-not $UseGlobalPython) {
    if ((Test-Path $VenvPython) -and -not (Test-PythonMatches $VenvPython)) {
        Write-Host "Existing .venv is not Python $WantMinor; rebuilding it for development..."
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }

    if (-not (Test-Path $VenvPython)) {
        $PyExe = $null
        $PyArgs = @()
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            try {
                & py.exe "-$WantMinor" --version *> $null
                if ($LASTEXITCODE -eq 0) {
                    $PyExe = "py.exe"
                    $PyArgs = @("-$WantMinor")
                }
            } catch {}
        }
        if ($null -eq $PyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
            if ((Get-PythonMinor "python") -eq $WantMinor) {
                $PyExe = "python"
                $PyArgs = @()
            }
        }
        if ($null -eq $PyExe) {
            throw "Could not find Python. Install Python $WantMinor or run the app launcher once to provision .venv."
        }
        Write-Host "Creating development environment at $VenvDir..."
        & $PyExe @PyArgs -m venv $VenvDir
    }
    $Python = $VenvPython
} else {
    $Python = "python"
}

$ActualMinor = Get-PythonMinor $Python
if ($ActualMinor -ne $WantMinor) {
    throw "Development setup requires Python $WantMinor, but $Python is $ActualMinor."
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt -r requirements-dev.txt

Write-Host ""
Write-Host "Developer environment ready."
Write-Host "Run checks with:"
Write-Host "  $Python -m pytest"
Write-Host "  $Python -m ruff check ."
Write-Host "  $Python -m mypy ."
