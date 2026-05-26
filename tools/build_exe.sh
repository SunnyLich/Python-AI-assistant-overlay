#!/usr/bin/env bash
set -euo pipefail

CLEAN=false
SKIP_INSTALL=false
YES=false
USE_GLOBAL_PYTHON=false
LITE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)    CLEAN=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --yes|-y)   YES=true ;;
        --use-global-python) USE_GLOBAL_PYTHON=true ;;
        --lite)     LITE=true ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if $LITE; then
    SPEC_NAME="WispLiteLinux.spec"
    APP_NAME="WispLite"
    REQUIREMENTS_FILE="requirements-light.txt"
else
    SPEC_NAME="WispLinux.spec"
    APP_NAME="Wisp"
    REQUIREMENTS_FILE="requirements.txt"
fi

SPEC="$ROOT/packaging/$SPEC_NAME"
DIST_BIN="$ROOT/dist/$APP_NAME/$APP_NAME"
ICON_PATH="$ROOT/assets/app.ico"
DOLL_ICON_PNG="$ROOT/assets/doll/idle.png"

cd "$ROOT"

confirm() {
    local prompt="$1"
    if $YES; then return 0; fi
    read -rp "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy](es)?$ ]]
}

if ! $USE_GLOBAL_PYTHON; then
    if [[ ! -x "$VENV_PYTHON" ]]; then
        if confirm "Project virtual environment not found at $VENV_DIR. Create it now?"; then
            python3 -m venv "$VENV_DIR"
        else
            echo "Build cancelled: project .venv is required unless you pass --use-global-python." >&2
            exit 1
        fi
    fi
    PYTHON="$VENV_PYTHON"
else
    echo "Using global Python because --use-global-python was provided."
    PYTHON="python3"
fi

if [[ ! -f "$ICON_PATH" ]]; then
    if [[ ! -f "$DOLL_ICON_PNG" ]]; then
        echo "Cannot create icon: doll source image missing at $DOLL_ICON_PNG" >&2
        exit 1
    fi
    echo "Creating exe icon from doll image: $ICON_PATH"
    "$PYTHON" -c "
from pathlib import Path
from PIL import Image
src = Path('$DOLL_ICON_PNG')
dst = Path('$ICON_PATH')
img = Image.open(src).convert('RGBA')
img.save(dst, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
"
fi

if [[ -f "$DIST_BIN" ]]; then
    if pgrep -x "$APP_NAME" > /dev/null 2>&1; then
        echo "Cannot rebuild while the packaged app is running. Close $APP_NAME first." >&2
        exit 1
    fi
fi

if $CLEAN; then
    rm -rf "$ROOT/build" "$ROOT/dist"
fi

if ! $SKIP_INSTALL; then
    if confirm "Install/update Python packages in $PYTHON before building?"; then
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install -r "$REQUIREMENTS_FILE" -r requirements-build.txt
    else
        echo "Skipping dependency install. Use --yes to install automatically or --skip-install to suppress this prompt."
    fi
fi

if ! "$PYTHON" -m PyInstaller --version > /dev/null 2>&1; then
    echo "PyInstaller is not installed. Run without --skip-install, or: $PYTHON -m pip install -r requirements-build.txt" >&2
    exit 1
fi

"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

echo ""
echo "Built app folder: $ROOT/dist/$APP_NAME"
echo "Executable:       $ROOT/dist/$APP_NAME/$APP_NAME"
