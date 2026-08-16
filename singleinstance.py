"""单实例锁：锁文件含 PID + 进程创建时间，存活检查防 PID 复用误判。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


class SingleInstance:
    def __init__(self, path: Path):
        self.path = path
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return True
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                pid = int(data.get("pid", 0))
                created = float(data.get("create_time", 0.0))
            except (OSError, ValueError, json.JSONDecodeError):
                pid, created = 0, 0.0
            if pid and self._process_matches(pid, created):
                return False  # 已有存活实例
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"pid": os.getpid(), "create_time": _create_time(os.getpid()), "ts": time.time()},
                    f,
                )
        except OSError:
            return False
        self._held = True
        return True

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._held = False

    @staticmethod
    def _process_matches(pid: int, created: float) -> bool:
        """PID 存活且创建时间吻合（防 PID 被复用）。"""
        if psutil is None:
            if os.name == "posix":
                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    return False
            return False
        try:
            proc = psutil.Process(pid)
            if not proc.is_running():
                return False
            actual = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return False
        if created <= 0:
            return True
        return abs(actual - created) < 5.0


def _create_time(pid: int) -> float:
    try:
        if psutil is not None:
            return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    return 0.0
