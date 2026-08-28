#!/usr/bin/env bash
# Sets up Canon Keeper on macOS and Linux: checks Python, builds a virtualenv,
# installs the app and its dependencies.
#
#   ./install.sh              # just the app
#   ./install.sh --dev        # plus pytest and pytest-qt
#   ./install.sh --whisper    # plus local transcription (large download)

set -euo pipefail

cd "$(dirname "$0")"

VENV_PATH=".venv"
EXTRAS=()

for arg in "$@"; do
    case "$arg" in
        --dev)     EXTRAS+=("dev") ;;
        --whisper) EXTRAS+=("whisper") ;;
        --venv=*)  VENV_PATH="${arg#*=}" ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m    %s\033[0m\n' "$1"; }
die()  { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. Find a suitable Python -----------------------------------------------
step "Looking for Python 3.11 or newer"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
        continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        ok "Found $("$candidate" --version) at $(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo
    echo "Python 3.11 or newer was not found." >&2
    case "$(uname -s)" in
        Darwin) echo "  brew install python@3.12    (or https://www.python.org/downloads/macos/)" >&2 ;;
        *)      echo "  sudo apt install python3 python3-venv    # Debian/Ubuntu" >&2
                echo "  sudo dnf install python3                 # Fedora" >&2 ;;
    esac
    exit 1
fi

# Debian and Ubuntu split venv into its own package, and the failure message
# from `python -m venv` alone is not obvious.
if ! "$PYTHON" -c "import venv" >/dev/null 2>&1; then
    die "Your Python has no 'venv' module. On Debian/Ubuntu: sudo apt install python3-venv"
fi

# --- 2. Create the virtual environment ---------------------------------------
if [ -x "$VENV_PATH/bin/python" ]; then
    step "Reusing the existing virtualenv at $VENV_PATH"
else
    step "Creating a virtualenv at $VENV_PATH"
    "$PYTHON" -m venv "$VENV_PATH"
fi
VENV_PYTHON="$VENV_PATH/bin/python"

# --- 3. Install ---------------------------------------------------------------
step "Upgrading pip"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

TARGET="."
if [ ${#EXTRAS[@]} -gt 0 ]; then
    IFS=,; TARGET=".[${EXTRAS[*]}]"; unset IFS
fi

step "Installing Canon Keeper and its dependencies (this downloads Qt; give it a minute)"
"$VENV_PYTHON" -m pip install -e "$TARGET"

# --- 4. Verify ----------------------------------------------------------------
step "Verifying the install"
if ! QT_QPA_PLATFORM=offscreen "$VENV_PYTHON" -c \
    "import canon_keeper, PySide6; print('Canon Keeper', canon_keeper.__version__, '/ PySide6', PySide6.__version__)"; then
    echo >&2
    echo "The package installed but Qt could not start." >&2
    echo "On a bare Linux box Qt needs some system libraries:" >&2
    echo "  sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3 \\" >&2
    echo "                   libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0" >&2
    exit 1
fi

echo
ok "Done. Start Canon Keeper with:"
echo "    ./$VENV_PATH/bin/canonkeeper"
echo "  or, from VS Code, press F5 and pick 'Canon Keeper'."
