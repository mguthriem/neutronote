#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# neutroNote installer – adds the `neutronote` command to your shell.
#
# Usage:  bash /SNS/SNAP/shared/deploy/neutronote/install.sh
#
# What it does:
#   1. Appends a small shell function to your ~/.bashrc
#   2. Sources it so the command is available immediately
#
# After installation, start a notebook with:
#   neutronote <IPTS_NUMBER>       e.g.  neutronote 33219
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

PROJDIR="/SNS/SNAP/shared/deploy/neutronote"
MARKER="# >>> neutronote >>>"
ENDMARKER="# <<< neutronote <<<"
RC="$HOME/.bashrc"

# ── Check that the project directory exists ──
if [ ! -d "$PROJDIR" ]; then
    echo "ERROR: Project directory not found: $PROJDIR" >&2
    exit 1
fi

# ── Remove any previous installation block ──
if grep -qF "$MARKER" "$RC" 2>/dev/null; then
    echo "Updating existing neutronote block in $RC ..."
    # Remove old block (between markers, inclusive)
    sed -i "/$MARKER/,/$ENDMARKER/d" "$RC"
else
    echo "Installing neutronote command into $RC ..."
fi

# ── Append the function ──
cat >> "$RC" << 'BLOCK'

# >>> neutronote >>>
# Added by neutroNote installer – do not edit between the markers.
neutronote() {
    local projdir="/SNS/SNAP/shared/deploy/neutronote"
    if [ -z "${1:-}" ]; then
        echo "Usage: neutronote <IPTS_NUMBER>"
        echo "  e.g. neutronote 33219"
        return 1
    fi
    PYTHONPATH="$projdir" pixi run --frozen --manifest-path "$projdir/pyproject.toml" \
        python -m neutronote.app --quiet --ipts "$1"
}
# <<< neutronote <<<
BLOCK

# ── Source it so it's available right now ──
# Temporarily disable nounset (-u) because the system /etc/bashrc
# on some analysis nodes uses variables without checking if they are
# set (e.g. BASHRCSOURCED), which crashes under `set -u`.
# shellcheck disable=SC1090
set +u
source "$RC" 2>/dev/null || true
set -u

echo ""
echo "✅ Done!  The 'neutronote' command is now available."
echo ""
echo "   Usage:   neutronote <IPTS_NUMBER>"
echo "   Example: neutronote 33219"
echo ""
