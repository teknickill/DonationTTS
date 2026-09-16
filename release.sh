#!/usr/bin/env bash
set -e

# --- CONFIGURATION ---
SOURCE_DIR="E:/Henry remake"
UPDATER_FILE="updater.py"
ISS_FILE="$SOURCE_DIR/installer.iss"                  # Fix if named differently
BUILD_EXE="$SOURCE_DIR/Output/StreamSuiteSetup.exe"   # Path to compiled output
LOCAL_EXE="StreamSuiteSetup.exe"

echo "=== Stream Suite Release Automation ==="

# 1. Ask for target version
read -p "Enter the NEW version tag (e.g. 1.2.2): " VERSION

if [ -z "$VERSION" ]; then
    echo "Error: Version cannot be empty."
    exit 1
fi

# 2. Sync latest updater.py from source folder
echo "[1/6] Syncing $UPDATER_FILE from source directory..."
if [ -f "$SOURCE_DIR/$UPDATER_FILE" ]; then
    cp "$SOURCE_DIR/$UPDATER_FILE" "./$UPDATER_FILE"
    echo " -> $UPDATER_FILE synced."
fi

# 3. Check updater.py for version match
echo "[2/6] Validating $UPDATER_FILE..."
if ! grep -q "APP_VERSION = \"$VERSION\"" "$UPDATER_FILE"; then
    echo "ERROR: $UPDATER_FILE does not have APP_VERSION set to \"$VERSION\"!"
    echo "Please update APP_VERSION in $UPDATER_FILE first."
    exit 1
fi
echo " -> $UPDATER_FILE is valid."

# 4. Check .iss file for version match
echo "[3/6] Validating Inno Setup script..."
if [ -f "$ISS_FILE" ]; then
    if ! grep -q "$VERSION" "$ISS_FILE"; then
        echo "ERROR: $ISS_FILE does not contain version string \"$VERSION\"!"
        echo "Please update your version string in $ISS_FILE first."
        exit 1
    fi
    echo " -> $ISS_FILE is valid."
else
    echo "WARNING: $ISS_FILE not found at $ISS_FILE. Skipping check."
fi

# 5. Pull fresh executable from source
echo "[4/6] Copying fresh installer..."
if [ -f "$BUILD_EXE" ]; then
    cp "$BUILD_EXE" "./$LOCAL_EXE"
    echo " -> Successfully copied $LOCAL_EXE"
else
    echo "ERROR: $BUILD_EXE not found!"
    echo "Please rebuild your installer in Inno Setup first."
    exit 1
fi

# 6. Clear stale git lock files
rm -f .git/index.lock

# 7. Commit, Tag & Push
echo "[5/6] Committing code & pushing tag v$VERSION to Git..."
git add .
git commit -m "Release v$VERSION" || echo "No new code changes to commit."
git tag -a "$VERSION" -m "Version $VERSION" || true
git push origin main
git push origin "$VERSION"

# 8. Create GitHub Release & Upload Asset
echo "[6/6] Publishing GitHub Release..."
gh release create "$VERSION" "$LOCAL_EXE" --title "v$VERSION Release" --notes "Release version $VERSION" --clobber

echo ""
echo "=== Success! Release $VERSION published automatically! ==="