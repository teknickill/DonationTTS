"""
updater.py
==========
Background auto-update checker/installer for the combined Stream Suite
launcher (combined_launcher.py). GitHub Releases as the source, "silent
download now, install on next restart" as the behavior:

  - check_and_stage_update_once() runs on a background thread (or via
    the tray's "Check for Updates Now"), and if a newer release exists,
    downloads its installer to a staging folder -- but NEVER runs it
    while the app is live. Nothing about a running session is ever
    interrupted by this.
  - The ONLY place a staged installer is actually run is
    apply_pending_update_if_any(), which combined_launcher.py's main()
    calls as the literal first thing it does -- before the
    single-instance lock, before either app's network clients start,
    before anything is holding a file or a port an installer would need
    to replace.

State that has to survive a restart -- which version is staged, where
its installer landed -- is written to a small JSON file in
%LOCALAPPDATA%\\StreamSuite (see _staging_dir()), not next to the exe:
the install location (Program Files, if the .iss requires admin) is not
reliably writable by a normal user process, and %LOCALAPPDATA% always is,
regardless of where or how this got installed.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# --- CONFIGURATION ---
# APP_VERSION used to be hand-bumped here on every release -- forget
# that step and the running exe permanently thinks it's outdated,
# re-downloading the SAME installer it's already running on every
# check cycle. It's now generated into _version.py by
# build_streamsuite.bat, read straight out of StreamSuite.iss's
# #define MyAppVersion, so there's exactly one number to keep in sync
# instead of two. See _version.py's own docstring/comment for what
# happens if this import ever runs against a stale/placeholder copy.
from _version import APP_VERSION
GITHUB_REPO = "teknickill/DonationTTS"  # owner/repo
ASSET_NAME = "StreamSuiteSetup.exe"     # must match the Release asset's filename EXACTLY

CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # how often the background loop re-checks
_INITIAL_DELAY_SECONDS = 30             # so the first check never competes with startup

# Set via set_tray_icon() once combined_launcher.py has actually built its
# pystray.Icon -- which happens AFTER start_background_update_checker() is
# called, so this starts as None and is only read (never assumed present)
# at the moment a notification would actually fire. Same pattern as
# henry_agent.set_tray_icon().
_tray_icon = None


def set_tray_icon(icon) -> None:
    """Lets combined_launcher.py hand over its tray icon so an update
    found / staged moment can surface as a real OS notification instead
    of only a print() that goes nowhere in a windowed build. Optional --
    every notification falls back to print()-only if this is never
    called, so running this module standalone (`python updater.py`)
    still works with no GUI at all."""
    global _tray_icon
    _tray_icon = icon


def _notify(title: str, message: str) -> None:
    print(f"[Updater] {title}: {message}")
    if _tray_icon is not None:
        try:
            _tray_icon.notify(message, title)
        except Exception:
            pass


def _staging_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, "StreamSuite", "update_staging")
    os.makedirs(path, exist_ok=True)
    return path


def _state_path() -> str:
    return os.path.join(_staging_dir(), "update_state.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[Updater] Couldn't save update state: {e}")


def _version_tuple(v: str):
    """"1.2.10" -> (1, 2, 10), so versions compare NUMERICALLY.

    Plain string inequality (what this file used to do) has two bugs:
    it fires on ANY difference, including one where the local version is
    NEWER than the remote tag (a downgrade), and it can't order
    multi-digit segments correctly if ever compared lexicographically
    ("1.10" < "1.9" as strings). Tuple comparison fixes both."""
    parts = []
    for segment in v.strip().lstrip("v").split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _is_newer(remote: str, current: str) -> bool:
    if not remote:
        return False
    return _version_tuple(remote) > _version_tuple(current)


def _find_asset_url(release: dict) -> "str | None":
    for asset in release.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return asset.get("browser_download_url")
    return None


def check_and_stage_update_once(manual: bool = False) -> None:
    """Checks GitHub once. If a newer release exists, downloads its
    installer to the staging folder and records that in _state_path() --
    and stops there. Never runs anything.

    manual: True when this came from the tray's "Check for Updates Now"
    (a person is actively waiting on an answer), False for the silent
    24h background loop. Only affects the "up to date" notification --
    see below for why that one alone is gated on it.

    Safe to call repeatedly (the background loop and "Check for Updates
    Now" alike) -- always re-reads state from disk first, so a version
    already staged from an earlier check is never re-downloaded. This is
    what fixes the "2 downloads" symptom: the previous version had no
    persisted state at all, so every check that found a newer tag
    downloaded it again from scratch, even if the last check had already
    grabbed the exact same file moments earlier."""
    state = _load_state()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                print(f"[Updater] Failed to check for updates. Status code: {response.status}")
                return
            release = json.loads(response.read().decode())
    except Exception as e:
        print(f"[Updater] Error checking for updates: {e}")
        return

    remote_version = release.get("tag_name", "").lstrip("v")
    print(f"[Updater] Current version: {APP_VERSION}")
    print(f"[Updater] Latest release: {remote_version or '?'}")

    if not _is_newer(remote_version, APP_VERSION):
        print("[Updater] Stream Suite is up to date!")
        # Gated on manual: the background loop runs every 24h forever --
        # a toast for "still up to date" on every silent check would be
        # noise, not signal. The tray's explicit "Check for Updates Now"
        # is someone waiting on an answer either way, so that one always
        # gets a visible response.
        if manual:
            _notify("Up to date", f"Stream Suite {APP_VERSION} is the latest version.")
        return

    print(f"[Updater] New update available: v{remote_version}")

    staged_path = state.get("pending_installer_path")
    if (state.get("pending_version") == remote_version
            and staged_path and os.path.isfile(staged_path)):
        print(f"[Updater] {remote_version} already staged, waiting for next restart.")
        return

    download_url = _find_asset_url(release)
    if not download_url:
        print(f"[Updater] Error: Asset {ASSET_NAME!r} not found in latest release.")
        return

    _notify("Update found", f"Downloading Stream Suite {remote_version}\u2026")

    dest_path = os.path.join(_staging_dir(), ASSET_NAME)
    tmp_path = dest_path + ".part"
    print(f"[Updater] Downloading update from: {download_url}")
    print(f"[Updater] Saving to: {dest_path}")
    try:
        urllib.request.urlretrieve(download_url, tmp_path)
        # Only renamed into place once the download is FULLY complete --
        # so a half-finished .part file left by an interrupted download
        # (network drop, app closed mid-download) can never be mistaken
        # for a valid staged installer by a later check or by
        # apply_pending_update_if_any().
        os.replace(tmp_path, dest_path)
    except Exception as e:
        print(f"[Updater] Failed to download update installer: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return

    state["pending_version"] = remote_version
    state["pending_installer_path"] = dest_path
    _save_state(state)
    print(f"[Updater] {remote_version} downloaded and staged -- will install "
          f"automatically the next time Stream Suite starts.")
    _notify("Update ready", f"Stream Suite {remote_version} will install on next launch.")


def start_background_update_checker() -> None:
    """Daemon thread: waits _INITIAL_DELAY_SECONDS so the very first
    check never competes with startup, then calls
    check_and_stage_update_once() on a loop, forever, every
    CHECK_INTERVAL_SECONDS. Never blocks the caller."""
    def _loop():
        time.sleep(_INITIAL_DELAY_SECONDS)
        while True:
            try:
                check_and_stage_update_once()
            except Exception as e:
                print(f"[Updater] Background check failed: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()


def apply_pending_update_if_any() -> bool:
    """The ONLY place a staged installer is actually run. Called as the
    literal first thing in combined_launcher.py's main(), before
    anything else has started -- which is exactly why this is safe to
    run SYNCHRONOUSLY and BLOCK until the installer finishes: there is
    nothing else alive yet that blocking would make unresponsive.

    Runs the installer with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART --
    no installer UI, no "please close this application" prompt (the
    previous version's actual bug: it launched the installer via
    Popen() and called sys.exit() from a BACKGROUND thread, which only
    ends that one thread, not the process -- so the app was still
    running and holding its own files when Inno Setup went to replace
    them, and Inno's own "close the running app" check is what you saw).

    One caveat this can't paper over: if the installer requires admin
    (PrivilegesRequired=admin in the .iss), Windows will still show ONE
    UAC elevation prompt -- that's the OS, not the installer's own UI,
    and /VERYSILENT doesn't suppress it. I don't have installer.iss in
    this conversation to confirm one way or the other.

    Relaunches via sys.executable afterward, which in a frozen build IS
    the just-overwritten exe's own path, then os._exit()s -- guaranteed
    process termination, unlike the old sys.exit() bug, so there's never
    a moment with two copies of this app alive at once.

    Returns False, having changed nothing, if there's no pending update.
    If an update WAS applied, this process is already gone by the time a
    caller could inspect a return value -- os._exit() never returns."""
    state = _load_state()
    path = state.get("pending_installer_path")
    if not path or not os.path.isfile(path):
        return False

    print(f"[Updater] Installing staged update: {path}")
    try:
        subprocess.run([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=False)
    except Exception as e:
        # Don't let a broken staged installer block startup forever --
        # log it, clear the pending state so this doesn't keep retrying
        # the same broken file on every future launch, and fall through
        # to starting the CURRENT (un-updated) version normally.
        print(f"[Updater] Failed to run staged installer: {e}")
        state.pop("pending_version", None)
        state.pop("pending_installer_path", None)
        _save_state(state)
        return False

    try:
        os.remove(path)
    except OSError:
        pass
    state.pop("pending_version", None)
    state.pop("pending_installer_path", None)
    _save_state(state)

    print("[Updater] Update applied -- relaunching.")
    try:
        subprocess.Popen([sys.executable])
    except Exception as e:
        print(f"[Updater] Couldn't relaunch after update: {e}")
    os._exit(0)


if __name__ == "__main__":
    check_and_stage_update_once()
