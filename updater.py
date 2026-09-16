"""
updater.py
==========
Background auto-update checker/installer for the combined Stream Suite
launcher (combined_launcher.py). GitHub Releases as the source, "silent
download now, install on next restart" as the behavior:

  - A background thread periodically checks GitHub for a newer release
    and, if one exists, downloads its installer to a staging folder --
    but never runs it while the app is live. Nothing about a running
    session is ever interrupted by this.
  - The ONLY place the staged installer is actually run is the very
    first thing combined_launcher.py's main() does, next launch --
    before the single-instance lock, before either app's network
    clients start, before anything is holding a file or a port that an
    installer would need to replace.

This deliberately does NOT do a fully-silent auto-restart mid-session
(the riskiest of the three options): an update landing in the middle of
a donation readout, with no warning, would kill audio playback on a
live stream with nothing else to explain why. Applying only at the
next natural restart avoids that entirely.

Settings live in the shared app_config.json under a NEW top-level
"update" key, sitting alongside "donation_engine" and "henry". Neither
app's own save_config() touches any key but its own (see main.py's
save_config() docstring) -- so this is safe to read/write independently
without racing either app's settings window.

ONE THING YOU MUST SET before this does anything: DEFAULT_UPDATE_CONFIG
below has "github_repo": "" -- there is no way to guess which GitHub
repo your releases live in. Either hand-edit app_config.json's new
"update" section once ({"github_repo": "yourname/yourrepo", ...}), or
ask for a small Settings field to set it from the UI instead.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import requests

import main as donation_engine

# Bump this by hand with every release you tag on GitHub. There is no
# way to derive this automatically -- it has to match, exactly, what
# you're about to compare it against (the tag on the release you cut).
APP_VERSION = "1.2.2"

DEFAULT_UPDATE_CONFIG = {
    "enabled": True,
    # REQUIRED, see module docstring. Format: "yourname/yourrepo".
    "github_repo": "teknickill/DonationTTS",
    "check_interval_hours": 12,
    # Matched against each release asset's filename, case-insensitive
    # substring -- picks the actual installer out of a release that
    # might also have source zips or other assets attached. Adjust if
    # your Inno Setup output filename changes.
    "asset_name_contains": "StreamSuiteSetup.exe",
    # The rest are written by this module, not meant to be hand-edited:
    "last_check_utc": None,
    "pending_version": None,
    "pending_installer_path": None,
}

_STAGING_DIRNAME = "pending_update"


# ---------------------------------------------------------------------------
# Config -- reuses main.py's shared-file reader/writer so this never
# fights either app's own save_config() over the same file. See that
# module's _read_shared_config_file()/save_config() docstrings.
# ---------------------------------------------------------------------------

def _load_update_config() -> dict:
    full = donation_engine._read_shared_config_file()
    cfg = dict(DEFAULT_UPDATE_CONFIG)
    cfg.update(full.get("update") or {})
    return cfg


def _save_update_config(cfg: dict) -> None:
    full = donation_engine._read_shared_config_file()
    full["update"] = cfg
    with open(donation_engine.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2)


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def _parse_version(v: str):
    """"v1.2.3" or "1.2.3" -> (1, 2, 3). Falls back to the raw string
    (still comparable, just not numerically) if it doesn't look like a
    dotted version -- so a weird tag name can't crash the check, it
    just won't compare usefully against a numeric APP_VERSION."""
    v = (v or "").strip()
    if v.lower().startswith("v"):
        v = v[1:]
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return v


def _is_newer(remote: str, local: str) -> bool:
    r, l = _parse_version(remote), _parse_version(local)
    try:
        return r > l
    except TypeError:
        # One parsed as a tuple, the other didn't -- can't compare
        # meaningfully. Treat as "not newer" rather than guessing.
        return False


# ---------------------------------------------------------------------------
# Checking + downloading (background thread, never blocks the app)
# ---------------------------------------------------------------------------

def _staging_dir() -> str:
    d = os.path.join(os.path.dirname(donation_engine.CONFIG_PATH), _STAGING_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _find_installer_asset(release: dict, name_contains: str):
    needle = (name_contains or "").lower()
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if needle in name.lower():
            return asset
    return None


def check_and_stage_update_once() -> None:
    """One check-and-maybe-download pass. Safe to call from a timer
    loop or a "Check for Updates Now" button alike -- always re-reads
    config first, so a repo/interval change takes effect on the very
    next call with no restart needed."""
    cfg = _load_update_config()
    if not cfg.get("enabled", True):
        return
    repo = (cfg.get("github_repo") or "").strip()
    if not repo:
        print("[Updater] No github_repo configured yet -- skipping check. "
              "Set update.github_repo in app_config.json to \"yourname/yourrepo\".")
        return

    cfg["last_check_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"}, timeout=15)
        resp.raise_for_status()
        release = resp.json()
    except Exception as e:
        print(f"[Updater] Couldn't check GitHub for updates: {e}")
        _save_update_config(cfg)
        return

    remote_version = release.get("tag_name", "")
    if not _is_newer(remote_version, APP_VERSION):
        print(f"[Updater] Up to date (running {APP_VERSION}, latest is {remote_version or '?'}).")
        _save_update_config(cfg)
        return

    # Already staged this exact version from an earlier check -- don't
    # re-download every interval until it's actually installed.
    if (cfg.get("pending_version") == remote_version
            and cfg.get("pending_installer_path")
            and os.path.isfile(cfg["pending_installer_path"])):
        print(f"[Updater] {remote_version} already staged, waiting for next restart.")
        _save_update_config(cfg)
        return

    asset = _find_installer_asset(release, cfg.get("asset_name_contains", "Setup.exe"))
    if asset is None:
        print(f"[Updater] Found {remote_version} but no asset matching "
              f"{cfg.get('asset_name_contains')!r} -- can't download it. "
              f"Check update.asset_name_contains against your actual release assets.")
        _save_update_config(cfg)
        return

    dest_path = os.path.join(_staging_dir(), asset.get("name", "update_installer.exe"))
    print(f"[Updater] Downloading {remote_version} ({asset.get('name')})\u2026")
    try:
        with requests.get(asset["browser_download_url"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
    except Exception as e:
        print(f"[Updater] Download failed: {e}")
        _save_update_config(cfg)
        return

    cfg["pending_version"] = remote_version
    cfg["pending_installer_path"] = dest_path
    _save_update_config(cfg)
    print(f"[Updater] {remote_version} downloaded and staged -- will install "
          f"automatically the next time Stream Suite starts.")


def start_background_update_checker() -> None:
    """Spawns the daemon thread that periodically calls
    check_and_stage_update_once(). Call this once, after startup's
    already-busy work (services, tray icon) is under way -- an update
    check is the lowest-priority thing happening at launch."""
    def worker():
        # A short initial wait so this never competes with everything
        # else spinning up in the first moments after launch.
        time.sleep(30)
        while True:
            check_and_stage_update_once()
            cfg = _load_update_config()
            hours = float(cfg.get("check_interval_hours", 24) or 24)
            time.sleep(max(hours, 0.1) * 3600)
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Applying a staged update -- ONLY ever called at the very start of
# main(), before anything else. See module docstring for why.
# ---------------------------------------------------------------------------

def apply_pending_update_if_any() -> bool:
    """If a previously-downloaded installer is staged and still on
    disk, runs it silently, then relaunches the app, then ends THIS
    process -- never returns in that case. Returns False (a normal,
    immediate return) if there's nothing staged, so the caller's
    startup continues as usual.

    Must run before acquire_single_instance_lock() and before any
    network client/server starts -- this process is either about to
    exit (update path) or about to proceed completely normally (no
    update path); there's no in-between state where it matters."""
    cfg = _load_update_config()
    installer_path = cfg.get("pending_installer_path")
    if not installer_path or not os.path.isfile(installer_path):
        return False

    print(f"[Updater] Installing staged update {cfg.get('pending_version')} now\u2026")

    # Clear the pending fields BEFORE launching the installer, not
    # after -- if the install or relaunch below fails for any reason,
    # the next startup should try a fresh check rather than looping on
    # a potentially broken installer forever.
    cfg["pending_version"] = None
    cfg["pending_installer_path"] = None
    _save_update_config(cfg)

    if getattr(sys, "frozen", False):
        relaunch_target = sys.executable
    else:
        # Running from source (no frozen exe to relaunch) -- nothing
        # sensible to hand the installer's follow-up command, so just
        # run it and let the person restart manually.
        relaunch_target = None

    try:
        if os.name == "nt":
            quoted_installer = f'"{installer_path}"'
            # Same detached-shell-with-a-delay pattern main.py's
            # restart_engine() already uses: run the installer silently,
            # THEN (only once it exits) start the relaunch target --
            # `&` chains them in the same shell so the second command
            # only fires after the first finishes.
            silent_flags = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
            if relaunch_target:
                cmd = f'{quoted_installer} {silent_flags} & start "" "{relaunch_target}"'
            else:
                cmd = f'{quoted_installer} {silent_flags}'
            subprocess.Popen(cmd, shell=True, close_fds=True,
                              creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        else:
            # Not actually a supported install target (Inno Setup is
            # Windows-only) but kept from crashing outright rather than
            # assuming os.name is always "nt".
            args = [installer_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
            subprocess.Popen(args, close_fds=True, start_new_session=True)
    except Exception as e:
        print(f"[Updater] Couldn't launch the staged installer: {e}")
        return False

    os._exit(0)
