#!/usr/bin/env bash
set -e

# --- CONFIGURATION ---
UPDATER_FILE="updater.py"
ISS_FILE="E:/Henry remake/installer.iss"          # Adjust filename/path if needed
BUILD_EXE="E:/Henry remake/Output/StreamSuiteSetup.exe" # Adjust Output folder if needed
LOCAL_EXE="StreamSuiteSetup.exe"

echo "=== Stream Suite Release Automation ==="

# 1. Ask for target version
read -p "Enter the NEW version tag (e.g. 1.2.1): " VERSION

if [ -z "$VERSION" ]; then
    echo "Error: Version cannot be empty."
    exit 1
fi

# 2. Check updater.py for version match
echo "[1/5] Checking $UPDATER_FILE..."
if ! grep -q "APP_VERSION = \"$VERSION\"" "$UPDATER_FILE"; then
    echo "ERROR: $UPDATER_FILE does not have APP_VERSION set to \"$VERSION\"!"
    echo "Please update APP_VERSION in $UPDATER_FILE first."
    exit 1
fi
echo " -> $UPDATER_FILE is up to date."

# 3. Check .iss file for version match
echo "[2/5] Checking $ISS_FILE..."
if [ -f "$ISS_FILE" ]; then
    if ! grep -q "$VERSION" "$ISS_FILE"; then
        echo "ERROR: $ISS_FILE does not contain version string \"$VERSION\"!"
        echo "Please update your version string in $ISS_FILE first."
        exit 1
    fi
    echo " -> $ISS_FILE is up to date."
else
    echo "WARNING: $ISS_FILE not found at $ISS_FILE. Skipping check."
fi

# 4. Copy fresh executable from build directory
echo "[3/5] Pulling fresh installer from Henry remake..."
if [ -f "$BUILD_EXE" ]; then
    cp "$BUILD_EXE" "./$LOCAL_EXE"
    echo " -> Successfully copied $LOCAL_EXE from $BUILD_EXE"
else
    echo "ERROR: $BUILD_EXE not found!"
    echo "Please rebuild your installer in Inno Setup first."
    exit 1
fi

# 5. Clear stale git lock files if present
if [ -f ".git/index.lock" ]; then
    rm -f .git/index.lock
fi

# 6. Commit code changes to Git
echo "[4/5] Committing code to Git..."
git add .
git commit -m "Release v$VERSION" || echo "No new code changes to commit."
git push origin main

# 7. Create GitHub Release & Upload Asset via gh CLI
echo "[5/5] Creating GitHub Release v$VERSION..."
gh release create "$VERSION" "$LOCAL_EXE" --title "v$VERSION Release" --notes "Release version $VERSION"

echo ""
echo "=== Success! Release $VERSION published & $LOCAL_EXE attached successfully! ==="