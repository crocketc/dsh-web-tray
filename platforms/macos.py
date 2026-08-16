"""macOS 特定实现：LaunchAgent（launchctl bootstrap/bootout，load/unload 已废弃）。"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import List

from platforms import AUTOSTART_NAME

LABEL = "com.dsh.webtray"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def set_autostart(enable: bool, invocation: List[str]) -> bool:
    plist_path = _plist_path()
    gui = f"gui/{os.getuid()}"

    if enable:
        # 先清掉旧注册（幂等）
        subprocess.run(
            ["launchctl", "bootout", gui, str(plist_path)], capture_output=True
        )
        plist_content = {
            "Label": LABEL,
            "ProgramArguments": list(invocation),
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(Path.home() / "Library" / "Logs" / f"{LABEL}.log"),
            "StandardErrorPath": str(Path.home() / "Library" / "Logs" / f"{LABEL}.err"),
        }
        try:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(plist_path, "wb") as f:
                plistlib.dump(plist_content, f)
        except OSError:
            return False
        result = subprocess.run(
            ["launchctl", "bootstrap", gui, str(plist_path)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    subprocess.run(["launchctl", "bootout", gui, str(plist_path)], capture_output=True)
    plist_path.unlink(missing_ok=True)
    return True


def is_autostart_enabled() -> bool:
    return _plist_path().exists()


def open_url(url: str) -> None:
    subprocess.run(["open", url], capture_output=True)


def reveal_path(path: str) -> None:
    subprocess.run(["open", "-R", path], capture_output=True)
