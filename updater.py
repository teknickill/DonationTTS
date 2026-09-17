import os
import sys
import urllib.request
import json
import subprocess
import tempfile

# --- CONFIGURATION ---
APP_VERSION = "1.2.1"                         # <--- Set lower than GitHub tag (e.g. 1.2.1) to test update catch!
GITHUB_REPO = "teknickill/DonationTTS"        # Repository path (owner/repo name)
EXE_NAME = "StreamSuiteSetup.exe"             # Asset name attached to GitHub release


def check_for_updates():
    """Checks GitHub releases API for a newer version than APP_VERSION."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            if response.status != 200:
                print(
                    f"Failed to check for updates. Status code: {response.status}"
                )
                return

            data = json.loads(response.read().decode())
            latest_version = data.get("tag_name", "").lstrip("v")

            print(f"Current version: {APP_VERSION}")
            print(f"Latest release: {latest_version}")

            if latest_version and latest_version != APP_VERSION:
                print(f"New update available: v{latest_version}")

                # Locate the .exe installer in assets
                download_url = None
                for asset in data.get("assets", []):
                    if asset.get("name") == EXE_NAME:
                        download_url = asset.get("browser_download_url")
                        break

                if download_url:
                    download_and_install(download_url)
                else:
                    print(
                        f"Error: Asset '{EXE_NAME}' not found in latest release."
                    )
            else:
                print("StreamSuite is up to date!")

    except Exception as e:
        print(f"Error checking for updates: {e}")


def download_and_install(download_url):
    """Downloads the setup .exe to a temp directory and launches it."""
    temp_dir = tempfile.gettempdir()
    installer_path = os.path.join(temp_dir, EXE_NAME)

    print(f"Downloading update from: {download_url}")
    print(f"Saving to: {installer_path}")

    try:
        # Download installer
        urllib.request.urlretrieve(download_url, installer_path)
        print("Download complete. Launching installer...")

        # Launch the installer silently/detached and exit app
        subprocess.Popen([installer_path], shell=True)
        sys.exit(0)

    except Exception as e:
        print(f"Failed to download or run update installer: {e}")


if __name__ == "__main__":
    check_for_updates()