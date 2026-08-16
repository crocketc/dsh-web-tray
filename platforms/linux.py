"""Linux 特定实现（可选支持）：XDG autostart + xdg-open。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from platforms import AUTOSTART_NAME

DESKTOP_NAME = "dsh-web-tray.desktop"


def _desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / DESKTOP_NAME


def _quote(parts: List[str]) -> str:
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def set_autostart(enable: bool, invocation: List[str]) -> bool:
    path = _desktop_path()
    if enable:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            exe, args = invocation[0], _quote(invocation[1:])
            path.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={AUTOSTART_NAME}\n"
                f"Exec={exe} {args}\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        except OSError:
            return False
        return True
    path.unlink(missing_ok=True)
    return True


def is_autostart_enabled() -> bool:
    return _desktop_path().exists()


def open_url(url: str) -> None:
    subprocess.run(["xdg-open", url], capture_output=True)


def reveal_path(path: str) -> None:
    subprocess.run(["xdg-open", path], capture_output=True)
