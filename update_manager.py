"""Update check manager for tray (ticket 04).

This module contains the update detection and notification logic that was
originally in dsh-web-tray.py but extracted for testability.
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Any, Dict, Optional

log = logging.getLogger("dsh-web-tray")

# Constants
AUTO_CHECK_COOLDOWN_SECONDS = 24 * 3600  # 24 hours
AUTO_CHECK_DELAY_SECONDS = 30  # 30 seconds after startup


def _should_check_for_update(cfg: Dict[str, Any], auto: bool) -> bool:
    """判断是否应当检查更新。

    Args:
        cfg: 配置字典（含 lastUpdateCheckAt）
        auto: 是否为自动检查（手动检查绕过冷却）

    Returns:
        True 表示应当检查
    """
    if not auto:
        # 手动检查绕过冷却时间
        return True
    
    # 自动检查：检查冷却时间
    last_check = cfg.get("lastUpdateCheckAt", 0)
    if last_check == 0:
        # 首次检查
        return True
    
    now = int(time.time())
    elapsed = now - last_check
    return elapsed >= AUTO_CHECK_COOLDOWN_SECONDS


def _update_last_check_time(cfg: Dict[str, Any]) -> None:
    """更新最后检查时间戳。"""
    cfg["lastUpdateCheckAt"] = int(time.time())


def _should_notify_update(cfg: Dict[str, Any], latest_version: str) -> bool:
    """判断是否应当通知更新（节流）。

    Args:
        cfg: 配置字典（含 lastNotifiedVersion）
        latest_version: 最新版本号

    Returns:
        True 表示应当通知
    """
    last_notified = cfg.get("lastNotifiedVersion", "")
    return last_notified != latest_version


def _record_notified_version(cfg: Dict[str, Any], version: str) -> None:
    """记录已通知的版本。"""
    cfg["lastNotifiedVersion"] = version


def _cache_latest_version(cfg: Dict[str, Any], version: str) -> None:
    """缓存最新版本号（菜单显示用）。"""
    cfg["lastKnownLatestVersion"] = version


class UpdateManager:
    """更新检测管理器：处理自动/手动检查、通知节流。"""

    def __init__(self, cfg: Dict[str, Any], check_fn, notify_fn):
        """初始化更新管理器。

        Args:
            cfg: 配置字典（会被修改以记录检查时间等）
            check_fn: 检查更新的函数，签名 cfg -> Dict[str, Any]
            notify_fn: 发送通知的函数，签名 (title: str, message: str) -> None
        """
        self.cfg = cfg
        self._check_fn = check_fn
        self._notify_fn = notify_fn
        self._lock = threading.Lock()
        self._upgrade_in_progress = False

    def check_now(self, auto: bool = True) -> Dict[str, Any]:
        """立即执行更新检查。

        Args:
            auto: 是否为自动检查

        Returns:
            检查结果字典
        """
        if not _should_check_for_update(self.cfg, auto):
            log.debug("跳过更新检查（冷却中或自动检查已冷却）")
            return {"has_update": False, "reason": "冷却中"}
        
        log.info("开始更新检查（auto=%s）", auto)
        result = self._check_fn(self.cfg)
        
        # 更新检查时间戳
        _update_last_check_time(self.cfg)
        
        # 处理检查结果
        has_update = result.get("has_update", False)
        if has_update:
            latest_version = result.get("latest_version") or "unknown"
            
            # 通知节流
            if _should_notify_update(self.cfg, latest_version):
                self._notify_fn(
                    "发现新版本",
                    f"DSH 有新版本可用：{latest_version}"
                )
                _record_notified_version(self.cfg, latest_version)
            
            # 缓存最新版本
            _cache_latest_version(self.cfg, latest_version)
            
            log.info("发现新版本：%s", latest_version)
        else:
            reason = result.get("reason", "未知")
            log.info("无更新：%s", reason)
            
            # 手动检查：即使无更新也通知用户
            if not auto:
                current_version = result.get("current_version") or "未知"
                self._notify_fn("检查更新", f"当前已是最新版本 {current_version}")
        
        return result

    def start_auto_check_scheduler(self, delay_seconds: int = AUTO_CHECK_DELAY_SECONDS) -> None:
        """启动自动检查调度器（后台线程）。

        Args:
            delay_seconds: 启动后延迟多少秒开始首次检查
        """
        def scheduler():
            time.sleep(delay_seconds)
            log.info("启动后自动更新检查")
            self.check_now(auto=True)
            
            # 后续每 24 小时检查一次
            while True:
                time.sleep(AUTO_CHECK_COOLDOWN_SECONDS)
                log.info("定期自动更新检查")
                self.check_now(auto=True)
        
        thread = threading.Thread(target=scheduler, name="update-check-scheduler", daemon=True)
        thread.start()

    def is_upgrade_in_progress(self) -> bool:
        """检查是否正在升级。"""
        with self._lock:
            return self._upgrade_in_progress

    def set_upgrade_in_progress(self, in_progress: bool) -> None:
        """设置升级状态。"""
        with self._lock:
            self._upgrade_in_progress = in_progress
