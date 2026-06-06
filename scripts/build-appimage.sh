#!/usr/bin/env bash
# build-appimage.sh — wrap the PyInstaller output into a Linux AppImage
#
# Prerequisites (installed by CI before this script runs):
#   - appimagetool  (downloaded as AppImage, placed in PATH)
#   - PyInstaller dist/gpredict-improved/ already built
#
# Output: dist/GPredict-Improved-x86_64.AppImage

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
COLLECT_DIR="$DIST_DIR/gpredict-improved"
APPDIR="$DIST_DIR/AppDir"

# --------------------------------------------------------------------------- #
# Sanity check
# --------------------------------------------------------------------------- #
if [[ ! -d "$COLLECT_DIR" ]]; then
    echo "ERROR: PyInstaller output not found at $COLLECT_DIR" >&2
    echo "       Run 'pyinstaller scripts/gpredict-improved.spec' first." >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# Build AppDir structure
# --------------------------------------------------------------------------- #
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy PyInstaller output into AppDir
cp -r "$COLLECT_DIR/." "$APPDIR/usr/bin/"

# AppRun entry point
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="$HERE/usr/bin:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/gpredict-improved" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# .desktop file (required by AppImage spec)
cat > "$APPDIR/usr/share/applications/gpredict-improved.desktop" << 'EOF'
[Desktop Entry]
Name=GPredict-Improved
Comment=Amateur Satellite Tracking
Exec=gpredict-improved
Icon=gpredict-improved
Type=Application
Categories=HamRadio;Science;
EOF
# Symlink to top-level (appimagetool expects .desktop at AppDir root)
ln -sf usr/share/applications/gpredict-improved.desktop "$APPDIR/gpredict-improved.desktop"

# Placeholder icon (256x256 PNG required; replace with real icon when available)
ICON_SRC="$REPO_ROOT/scripts/gpredict-improved.png"
if [[ -f "$ICON_SRC" ]]; then
    cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/gpredict-improved.png"
    ln -sf usr/share/icons/hicolor/256x256/apps/gpredict-improved.png "$APPDIR/gpredict-improved.png"
else
    # Generate a minimal placeholder PNG using Python + Pillow (available via packaging extras)
    python3 - << 'PYEOF'
from PIL import Image, ImageDraw
img = Image.new("RGBA", (256, 256), (20, 30, 50, 255))
d = ImageDraw.Draw(img)
d.ellipse([16, 16, 240, 240], outline=(88, 166, 255), width=8)
d.text((80, 110), "GP+", fill=(88, 166, 255))
import os, pathlib
out = pathlib.Path(os.environ.get("APPDIR_ICON",
    "dist/AppDir/usr/share/icons/hicolor/256x256/apps/gpredict-improved.png"))
out.parent.mkdir(parents=True, exist_ok=True)
img.save(str(out))
PYEOF
    export APPDIR_ICON="$APPDIR/usr/share/icons/hicolor/256x256/apps/gpredict-improved.png"
    ln -sf usr/share/icons/hicolor/256x256/apps/gpredict-improved.png "$APPDIR/gpredict-improved.png"
fi

# --------------------------------------------------------------------------- #
# Download appimagetool if not in PATH
# --------------------------------------------------------------------------- #
if ! command -v appimagetool &>/dev/null; then
    echo "Downloading appimagetool..."
    TOOL="$DIST_DIR/appimagetool-x86_64.AppImage"
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
    alias appimagetool="$TOOL"
    APPIMAGETOOL="$TOOL"
else
    APPIMAGETOOL="appimagetool"
fi

# --------------------------------------------------------------------------- #
# Build AppImage
# --------------------------------------------------------------------------- #
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$DIST_DIR/GPredict-Improved-x86_64.AppImage"

echo ""
echo "AppImage created: $DIST_DIR/GPredict-Improved-x86_64.AppImage"
