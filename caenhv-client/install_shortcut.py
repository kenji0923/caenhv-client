"""Create or remove a Windows Start-menu shortcut for the caenhv-client GUI.

Replaces the shortcut-creation role of the former desktop-app dependency.
Windows only; uses PowerShell's WScript.Shell COM object, so no extra
Python packages are required.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "CAEN HV Client.lnk"


def _start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable is not set")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _find_target() -> list[str]:
    if getattr(sys, "frozen", False):
        # PyInstaller build: the shortcut targets the executable itself.
        return [sys.executable]
    # Prefer the GUI-script exe shim (no console window).
    for script in ("caenhv-client-gui", "caenhv-client"):
        found = shutil.which(script)
        if found:
            return [found]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    return [interpreter, "-m", "caenhv_client"]


def _icon_location() -> str | None:
    if getattr(sys, "frozen", False):
        # PyInstaller onefile: package files live in a temp dir that is
        # deleted on exit, so a shortcut must not reference them. The exe
        # itself carries the embedded icon.
        return f"{sys.executable},0"
    icon = Path(__file__).resolve().parent / "resources" / "caenhv-client.ico"
    return str(icon) if icon.exists() else None


def install() -> Path:
    target = _find_target()
    shortcut = _start_menu_dir() / SHORTCUT_NAME
    lines = [
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{}')".format(shortcut),
        "$s.TargetPath = '{}'".format(target[0]),
    ]
    if len(target) > 1:
        lines.append("$s.Arguments = '{}'".format(" ".join(target[1:])))
    lines.append("$s.WorkingDirectory = '{}'".format(Path(target[0]).parent))
    icon = _icon_location()
    if icon is not None:
        lines.append("$s.IconLocation = '{}'".format(icon))
    lines.append("$s.Save()")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(lines)],
        check=True,
    )
    return shortcut


def remove() -> bool:
    shortcut = _start_menu_dir() / SHORTCUT_NAME
    if shortcut.exists():
        shortcut.unlink()
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or remove the Start-menu shortcut for the caenhv-client GUI (Windows only)."
    )
    parser.add_argument("--remove", action="store_true", help="remove the shortcut instead of creating it")
    args = parser.parse_args()
    if os.name != "nt":
        print("This command only works on Windows; nothing to do on this platform.")
        return 1
    if args.remove:
        if remove():
            print(f"Removed Start-menu shortcut '{SHORTCUT_NAME}'.")
        else:
            print(f"No Start-menu shortcut '{SHORTCUT_NAME}' found.")
        return 0
    shortcut = install()
    print(f"Created Start-menu shortcut: {shortcut}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
