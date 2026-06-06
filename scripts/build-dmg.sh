#!/usr/bin/env bash
# build-dmg.sh — create a macOS .dmg from the PyInstaller .app bundle
#
# Prerequisites:
#   - PyInstaller dist/GPredict-Improved.app already built
#
# Output: dist/GPredict-Improved.dmg

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
APP_BUNDLE="$DIST_DIR/GPredict-Improved.app"
DMG_PATH="$DIST_DIR/GPredict-Improved.dmg"
DMG_STAGING="$DIST_DIR/dmg-staging"

# --------------------------------------------------------------------------- #
# Sanity check
# --------------------------------------------------------------------------- #
if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "ERROR: .app bundle not found at $APP_BUNDLE" >&2
    echo "       Run 'pyinstaller scripts/gpredict-improved.spec' first." >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# Staging directory
# --------------------------------------------------------------------------- #
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -r "$APP_BUNDLE" "$DMG_STAGING/"
# Symlink to /Applications so the DMG shows a drag-install UI
ln -s /Applications "$DMG_STAGING/Applications"

# --------------------------------------------------------------------------- #
# Create DMG
# --------------------------------------------------------------------------- #
rm -f "$DMG_PATH"

hdiutil create \
    -volname "GPredict-Improved" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_STAGING"

echo ""
echo "DMG created: $DMG_PATH"
