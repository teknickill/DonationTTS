#!/usr/bin/env bash
set -e

# --- CONFIGURATION ---
SOURCE_DIR="E:/Henry remake"
UPDATER_FILE="updater.py"
ISS_FILE="$SOURCE_DIR/installer.iss"                  # Adjust if your .iss file has a different name
BUILD_EXE="$SOURCE_DIR/Output/StreamSuiteSetup.exe"   # Path where Inno Setup places output
LOCAL_EXE="StreamSuiteSetup.exe"

echo "=== Stream Suite Release Automation ==="

# 1. Prompt for target version tag
read -p "Enter the NEW version tag (e.g. 1.2.3): " VERSION

if [ -z "$VERSION" ]; then
    echo "Error: Version cannot be empty."
    exit 1
fi

# 2. Sync latest updater.py from source directory
echo "[1/6] Syncing $UPDATER_FILE from $SOURCE_DIR..."
if [ -f "$SOURCE_DIR/$UPDATER_FILE" ]; then
    cp "$SOURCE_DIR/$UPDATER_FILE" "./$UPDATER_FILE"
    echo " -> $UPDATER_FILE synced successfully."
else
    echo "WARNING: $SOURCE_DIR/$UPDATER_FILE not found. Skipping copy."
fi

# 3. Check updater.py for version match
echo "[2/6] Validating APP_VERSION in $UPDATER_FILE..."
if ! grep -q "APP_VERSION = \"$VERSION\"" "$UPDATER_FILE"; then
    echo "ERROR: $UPDATER_FILE does not have APP_VERSION set to \"$VERSION\"!"
    echo "Please update APP_VERSION in $UPDATER_FILE first."
    exit 1
fi
echo " -> $UPDATER_FILE version validated."

# 4. Check .iss file for version match
echo "[3/6] Validating Inno Setup installer script..."
if [ -f "$ISS_FILE" ]; then
    if ! grep -q "$VERSION" "$ISS_FILE"; then
        echo "ERROR: $ISS_FILE does not contain version string \"$VERSION\"!"
        echo "Please update your version string in $ISS_FILE first."
        exit 1
    fi
    echo " -> $ISS_FILE version validated."
else
    echo "WARNING: Inno Setup script not found at $ISS_FILE. Skipping check."
fi

# 5. Pull fresh executable from build directory
echo "[4/6] Copying fresh installer executable..."
if [ -f "$BUILD_EXE" ]; then
    cp "$BUILD_EXE" "./$LOCAL_EXE"
    echo " -> Successfully copied $LOCAL_EXE from build folder."
else
    echo "ERROR: $BUILD_EXE not found!"
    echo "Please rebuild your installer in Inno Setup first."
    exit 1
fi

# 6. Clear stale git lock files
if [ -f ".git/index.lock" ]; then
    rm -f .git/index.lock
fi

# 7. Commit, Tag & Push
echo "[5/6] Committing changes & pushing code to GitHub..."
git add .
git commit -m "Release v$VERSION" || echo "No new changes to commit."
git tag -a "$VERSION" -m "Version $VERSION" || echo "Tag $VERSION already exists locally."
git push origin main
git push origin "$VERSION" || echo "Tag $VERSION already exists on remote."

# 8. Create GitHub Release & Upload Asset
echo "[6/6] Publishing GitHub Release v$VERSION..."
if gh release view "$VERSION" >/dev/null 2>&1; then
    echo "Release $VERSION exists. Uploading/updating asset..."
    gh release upload "$VERSION" "$LOCAL_EXE" --clobber
    gh release edit "$VERSION" --draft=false
else
    gh release create "$VERSION" "$LOCAL_EXE" --title "v$VERSION Release" --notes "Release version $VERSION"
fi

echo ""
echo "=== Success! Release $VERSION published & $LOCAL_EXE attached successfully! ==="