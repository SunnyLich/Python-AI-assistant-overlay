#!/usr/bin/env bash
set -euo pipefail

CLEAN=false
SKIP_INSTALL=false
YES=false
USE_GLOBAL_PYTHON=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)    CLEAN=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --yes|-y)   YES=true ;;
        --use-global-python) USE_GLOBAL_PYTHON=true ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
WANT="$(cat "$ROOT/.python-version" 2>/dev/null || printf "3.12.13")"
WANT_MM="${WANT%.*}"

SPEC_NAME="WispLinux.spec"
APP_NAME="Wisp"
REQUIREMENTS_FILE="requirements.txt"

SPEC="$ROOT/packaging/$SPEC_NAME"
DIST_BIN="$ROOT/dist/$APP_NAME/$APP_NAME"
ICON_PATH="$ROOT/assets/app.ico"
ICON_SOURCE_PNG="$ROOT/assets/doll/idle.png"

cd "$ROOT"

py_minor() {
    "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

find_expected_python() {
    for cmd in "python$WANT_MM" python3 python; do
        if command -v "$cmd" >/dev/null 2>&1 && [[ "$(py_minor "$cmd")" == "$WANT_MM" ]]; then
            printf '%s\n' "$cmd"
            return 0
        fi
    done
    return 1
}

confirm() {
    local prompt="$1"
    if $YES; then return 0; fi
    read -rp "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy](es)?$ ]]
}

if ! $USE_GLOBAL_PYTHON; then
    if [[ ! -x "$VENV_PYTHON" ]]; then
        if confirm "Project virtual environment not found at $VENV_DIR. Create it now?"; then
            CREATE_PYTHON="$(find_expected_python || true)"
            if [[ -z "$CREATE_PYTHON" ]]; then
                echo "Could not find Python $WANT to create the project virtual environment." >&2
                exit 1
            fi
            "$CREATE_PYTHON" -m venv "$VENV_DIR"
        else
            echo "Build cancelled: project .venv is required unless you pass --use-global-python." >&2
            exit 1
        fi
    fi
    PYTHON="$VENV_PYTHON"
    HAVE_MM="$(py_minor "$PYTHON")"
    if [[ "$HAVE_MM" != "$WANT_MM" ]]; then
        echo "$PYTHON is Python $HAVE_MM, but Wisp packaging is pinned to Python $WANT." >&2
        echo "Rebuild .venv with scripts/setup_dev.sh or rerun the launcher with Python $WANT installed." >&2
        exit 1
    fi
else
    echo "Using global Python because --use-global-python was provided."
    PYTHON="python3"
    HAVE_MM="$(py_minor "$PYTHON")"
    if [[ "$HAVE_MM" != "$WANT_MM" ]]; then
        echo "$PYTHON is Python $HAVE_MM, but Wisp packaging is pinned to Python $WANT." >&2
        exit 1
    fi
fi

if [[ ! -f "$ICON_PATH" ]]; then
    if [[ ! -f "$ICON_SOURCE_PNG" ]]; then
        echo "Cannot create icon: icon source image missing at $ICON_SOURCE_PNG" >&2
        exit 1
    fi
    echo "Creating exe icon from icon image: $ICON_PATH"
    "$PYTHON" -c "
from pathlib import Path
from PIL import Image
src = Path('$ICON_SOURCE_PNG')
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

# Seed the user config dir (~/.config/wisp/.env) with the repo's .env if the
# user has no settings yet. The app reads/writes settings there at runtime so
# they survive rebuilds and updates.
XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
USER_CFG="$XDG_CFG/wisp"
ENV_TARGET="$USER_CFG/.env"
mkdir -p "$USER_CFG"
if [[ ! -f "$ENV_TARGET" ]]; then
    cp "$ROOT/.env" "$ENV_TARGET" 2>/dev/null \
        || cp "$ROOT/.env.example" "$ENV_TARGET" 2>/dev/null \
        || touch "$ENV_TARGET"
    echo "Created $ENV_TARGET (initial settings)"
else
    echo "Keeping existing settings at $ENV_TARGET"
fi

echo ""
echo "Built app folder: $ROOT/dist/$APP_NAME"
echo "Executable:       $ROOT/dist/$APP_NAME/$APP_NAME"
echo "Settings file:    $ENV_TARGET  (persists across rebuilds)"
