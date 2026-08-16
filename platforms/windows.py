"""Windows 特定实现：注册表自启 + 资源管理器/浏览器。"""
from __future__ import annotations

import os
import subprocess
from typing import List

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

try:
    import winreg
except ImportError:  # 非 Windows 环境导入本模块（如 CI lint）
    winreg = None  # type: ignore[assignment]

from platforms import AUTOSTART_NAME


def set_autostart(enable: bool, invocation: List[str]) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                # 列表参数拼成带引号的命令行（路径含空格安全）
                cmd = " ".join(f'"{part}"' if " " in part else part for part in invocation)
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def is_autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def open_url(url: str) -> None:
    os.startfile(url)  # noqa: S606 - 默认浏览器


def reveal_path(path: str) -> None:
    if os.path.isdir(path):
        subprocess.run(["explorer", path], capture_output=True)
    else:
        subprocess.run(["explorer", "/select,", path], capture_output=True)
