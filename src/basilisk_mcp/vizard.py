"""Find and launch Basilisk's Vizard 3-D visualizer on a recorded ``vizard.bin`` file.

Vizard is a separate desktop application (https://avslab.github.io/basilisk/Vizard/VizardDownload.html).
It is launched with its documented ``-loadFile <path>`` argument.

Security: the executable comes only from the server configuration or a fixed list of
standard install locations, never from a tool argument; the only argument passed is the
path of a ``vizard.bin`` file that this server wrote inside its data directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import Settings
from .errors import BskMcpError

DOWNLOAD_URL = {
    "win32": "https://hanspeterschaub.info/bskFiles/Vizard_Windows64.zip",
    "darwin": "https://hanspeterschaub.info/bskFiles/Vizard_macOS.zip",
    "linux": "https://hanspeterschaub.info/bskFiles/Vizard_Linux.zip",
}


def download_url() -> str:
    return DOWNLOAD_URL.get(sys.platform, DOWNLOAD_URL["linux"])


def candidate_paths() -> list[Path]:
    """Standard places people unzip Vizard to."""
    home = Path.home()
    if sys.platform == "win32":
        roots = [home / "Vizard", home / "Vizard_Windows64", home / "Downloads" / "Vizard_Windows64",
                 home / "Downloads" / "Vizard", home / "Desktop" / "Vizard", home / "Desktop" / "Vizard_Windows64",
                 Path(os.environ.get("LOCALAPPDATA", home)) / "Programs" / "Vizard",
                 Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Vizard"]
        found = []
        for root in roots:
            found += [root / "Vizard.exe", *sorted(root.glob("*/Vizard.exe"))]
        return found
    if sys.platform == "darwin":
        return [Path("/Applications/Vizard.app"), home / "Applications" / "Vizard.app",
                home / "Downloads" / "Vizard.app"]
    roots = [home / "Vizard", home / "Vizard_Linux", home / "Downloads" / "Vizard_Linux", Path("/opt/Vizard")]
    found = []
    for root in roots:
        found += [root / "Vizard.x86_64", root / "Vizard"]
    return found


def find_vizard(settings: Settings) -> Path | None:
    if settings.vizard_path:
        path = Path(settings.vizard_path).expanduser()
        return path if path.exists() else None
    return next((p for p in candidate_paths() if p.exists()), None)


def not_found_error(settings: Settings) -> BskMcpError:
    where = f"vizard_path = {settings.vizard_path!r} does not exist" if settings.vizard_path \
        else "Vizard was not found in the standard locations"
    return BskMcpError(
        "VIZARD_NOT_FOUND",
        f"{where}.",
        f"Download Vizard from {download_url()}, unzip it (for example to your home folder as 'Vizard'), "
        "or set vizard_path in the basilisk-mcp config / BASILISK_MCP_VIZARD_PATH. "
        "The .bin file can also be opened manually from Vizard's start panel (Select → Start Visualization).",
    )


def launch(settings: Settings, bin_file: Path) -> dict:
    """Start Vizard detached from the server, loading ``bin_file``."""
    exe = find_vizard(settings)
    if exe is None:
        raise not_found_error(settings)
    bin_file = bin_file.resolve()
    if bin_file.suffix != ".bin" or not bin_file.is_file() or settings.data_dir not in bin_file.parents:
        raise BskMcpError("INVALID_ARGUMENT", "Only Vizard files written by this server can be opened.")
    if sys.platform == "darwin" and exe.suffix == ".app":
        command = ["open", "-n", "-a", str(exe), "--args", "-loadFile", str(bin_file)]
    else:
        command = [str(exe), "-loadFile", str(bin_file)]
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "cwd": str(exe.parent), "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed executable, server-owned file
    return {"launched": True, "vizard": str(exe), "file": str(bin_file), "pid": process.pid}
