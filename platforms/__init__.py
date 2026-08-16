"""平台特定实现的统一入口。

⚠️ 目录名必须是 ``platforms``，不能叫 ``platform/``——会遮蔽 Python 标准库
platform 模块，psutil/PIL 内部 import 会炸。
"""
from __future__ import annotations

import sys
from typing import List

#: 自启注册名（必须在子模块导入前定义，子模块 from platforms import AUTOSTART_NAME）
AUTOSTART_NAME = "DSHWebTray"

if sys.platform == "win32":
    from platforms import windows as platform_impl
elif sys.platform == "darwin":
    from platforms import macos as platform_impl
else:
    from platforms import linux as platform_impl


def set_autostart(enable: bool, invocation: List[str]) -> bool:
    """设置/取消开机自启（用户级）。invocation 来自 config.self_invocation()。"""
    return platform_impl.set_autostart(enable, invocation)


def is_autostart_enabled() -> bool:
    return platform_impl.is_autostart_enabled()


def open_url(url: str) -> None:
    platform_impl.open_url(url)


def reveal_path(path: str) -> None:
    """在文件管理器中打开/定位目录。"""
    platform_impl.reveal_path(path)
