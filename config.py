"""配置持久化（~/.dsh-web-tray/config.json）。

坑（P1）：命令必须存 **argv 数组**。存字符串再 .split() 解析，遇到
``C:\\Program Files\\...`` 这类带空格路径即碎。dshArgvDisplay 仅供 UI 展示。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_VERSION = 1

DEFAULT_PORT = 3080


def app_dir() -> Path:
    """应用数据目录（跨平台统一）。

    支持 ``DSH_WEB_TRAY_HOME`` 环境变量覆盖（测试/便携部署用）。
    """
    override = os.environ.get("DSH_WEB_TRAY_HOME", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".dsh-web-tray"


def config_path() -> Path:
    return app_dir() / "config.json"


def log_dir() -> Path:
    return app_dir() / "logs"


def dsh_log_path() -> Path:
    return log_dir() / "dsh-web.log"


def tray_log_path() -> Path:
    return log_dir() / "tray.log"


def default_config() -> Dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "dshType": "",                  # "pnpm" | "global" | "local" | "manual"
        "dshArgv": [],                  # 绝对路径 argv 数组（P1：不存字符串）
        "dshArgvDisplay": "",
        "dshDir": "",                   # spawn cwd（源码安装=repo 根）
        "dshPort": DEFAULT_PORT,        # 0 = 系统自动分配（从 URL 行回读）
        "lastUrl": "",
        "autostart": False,
    }


def load_config() -> Optional[Dict[str, Any]]:
    """加载配置；不存在或损坏返回 None（触发向导）。"""
    path = config_path()
    if not path.exists():
        return None
    try:
        # utf-8-sig：兼容 Windows 工具（PowerShell/notepad）写入的 UTF-8 BOM
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = default_config()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    if not _is_valid(cfg):
        return None
    return cfg


def save_config(cfg: Dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def _is_valid(cfg: Dict[str, Any]) -> bool:
    argv = cfg.get("dshArgv")
    if not isinstance(argv, list) or not argv:
        return False
    if not all(isinstance(x, str) and x for x in argv):
        return False
    port = cfg.get("dshPort")
    if not isinstance(port, int) or not (0 <= port <= 65535):
        return False
    d = cfg.get("dshDir")
    if d and not isinstance(d, str):
        return False
    return True


def build_argv_with_port(cfg: Dict[str, Any]) -> List[str]:
    """返回带端口参数的完整启动 argv。

    配置端口为 0 时也显式追加 ``--port 0``（URL 行会回读实际端口）；
    argv 里已有 --port 则不重复追加（commander 取后者，此处直接尊重原命令）。
    """
    argv: List[str] = list(cfg.get("dshArgv") or [])
    port = cfg.get("dshPort")
    if port is None or "--port" in argv:
        return argv
    return [*argv, "--port", str(port)]


def loopback_url_for_port(port: int) -> str:
    """外部启动场景的 URL 推算（dsh 仅绑定 loopback，源码刻意禁止 0.0.0.0）。"""
    return f"http://127.0.0.1:{port}"


def self_invocation() -> List[str]:
    """开机自启应执行的 argv。

    PyInstaller frozen 后 __file__ 指向临时解压目录（onefile 运行后删除），
    必须用 sys.executable。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).with_name("dsh-web-tray.py").resolve())]
