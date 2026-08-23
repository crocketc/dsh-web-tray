"""Update check manager for tray (ticket 04).

This module contains the update detection and notification logic that was
originally in dsh-web-tray.py but extracted for testability.
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Any, Dict

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




def build_upgrade_menu_item(
    check_result: Dict[str, Any],
    cfg: Dict[str, Any],
    upgrade_callback,
    upgrade_in_progress: bool = False,
):
    """构建升级菜单项。

    Args:
        check_result: check_for_update 的返回结果
        cfg: 配置字典
        upgrade_callback: 点击升级项时的回调函数
        upgrade_in_progress: 是否正在升级中

    Returns:
        pystray.MenuItem 或 None（无更新时）
    """
    import pystray
    
    has_update = check_result.get("has_update", False)
    if not has_update:
        return None
    
    dsh_type = cfg.get("dshType", "")
    latest_version = check_result.get("latest_version") or "最新版本"
    reason = check_result.get("reason", "")
    
    if upgrade_in_progress:
        # 升级中：显示"升级中…"并禁用
        return pystray.MenuItem(
            "🔄 升级中…",
            None,
            enabled=False,
        )
    
    # 根据安装类型生成不同的菜单项文本
    if dsh_type in ("global", "local"):
        # npm 安装：显示版本号
        text = f"🆕 升级到 {latest_version}"
    elif dsh_type == "manual":
        # manual：不显示版本号，提示打开发布页
        text = "🆕 有新版本（打开发布页）"
    else:  # pnpm
        # pnpm：显示提交数
        if "落后" in reason:
            # 提取提交数
            import re
            match = re.search(r'落后 (\d+) 提交', reason)
            if match:
                behind = match.group(1)
                text = f"🆕 有更新（落后 {behind} 提交）"
            else:
                text = "🆕 有新版本"
        else:
            text = "🆕 有新版本"
    
    return pystray.MenuItem(text, upgrade_callback)


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

    def handle_upgrade_result(self, success: bool, stderr_tail: str, state: str) -> None:
        """处理升级结果回调（票 05）。

        Args:
            success: 升级是否成功
            stderr_tail: 错误信息摘要（失败时）
            state: 当前 DSH 进程状态（running/starting/external/stopped/crashed/start_failed）

        行为：
        - 成功：发送「升级完成，建议重启」通知，根据状态显示/隐藏重启菜单项
        - external 态：通知提示「自行重启外部实例」，不显示一键重启
        - 失败：通知含错误摘要+手动命令，升级项保持可重试
        - 超时：特殊处理，清理升级状态
        - 成功后清除缓存键并触发重新检查
        """
        # 清除升级进行中状态
        self.set_upgrade_in_progress(False)
        
        if success:
            # 成功路径
            if state in ("running", "starting"):
                # 自管进程：显示「升级完成，建议重启」
                self._notify_fn(
                    "升级完成",
                    "升级成功！建议重启 DSH 以应用新版本。"
                )
                # 设置重启标记（供托盘显示重启菜单项）
                self.cfg["_showRestartAfterUpgrade"] = True
                log.info("升级成功，状态=%s，显示重启提示", state)
            elif state == "external":
                # 外部进程：提示自行重启
                self._notify_fn(
                    "升级完成",
                    "升级成功！由于 DSH 为外部启动，请自行重启 DSH 以应用新版本。"
                )
                log.info("升级成功，状态=external，提示用户自行重启")
            else:
                # 其他状态：通用提示
                self._notify_fn(
                    "升级完成",
                    "升级成功！请重启 DSH 以应用新版本。"
                )
                log.info("升级成功，状态=%s", state)
            
            # 清除缓存键
            self._clear_version_cache()
            
            # 触发重新检查版本
            self._trigger_version_recheck()
        else:
            # 失败路径
            # 判断是否为超时
            is_timeout = "超时" in stderr_tail or "timeout" in stderr_tail.lower()
            
            if is_timeout:
                # 超时处理
                self._notify_fn(
                    "升级超时",
                    f"升级超过30分钟未完成，已终止。{stderr_tail}"
                )
                log.warning("升级超时：%s", stderr_tail)
            else:
                # 一般失败：显示错误摘要+手动命令
                manual_command = self._get_manual_command()
                message = f"升级失败：{stderr_tail}\n\n手动命令：\n{manual_command}"
                self._notify_fn("升级失败", message)
                log.error("升级失败：%s", stderr_tail)
            
            # 失败后升级项保持可重试（已在 set_upgrade_in_progress(False) 中清理）
    
    def _clear_version_cache(self) -> None:
        """清除版本缓存键。"""
        self.cfg.pop("lastKnownLatestVersion", None)
        self.cfg.pop("lastNotifiedVersion", None)
        log.debug("已清除版本缓存键")
    
    def _trigger_version_recheck(self) -> None:
        """触发版本重新检查（后台线程，不阻塞）。"""
        def recheck_worker():
            try:
                log.info("触发升级后版本重新检查")
                self.check_now(auto=True)
            except Exception:
                log.exception("升级后版本重新检查失败")
        
        thread = threading.Thread(target=recheck_worker, name="upgrade-recheck", daemon=True)
        thread.start()
    
    def _get_manual_command(self) -> str:
        """获取手动升级命令文案（用于失败通知）。"""
        import updater
        
        dsh_type = self.cfg.get("dshType", "")
        latest_version = self.cfg.get("lastKnownLatestVersion", "latest")
        
        if dsh_type == "global":
            return f"npm install -g @deepseek-ai/dsh@{latest_version}"
        elif dsh_type == "local":
            dsh_dir = self.cfg.get("dshDir", "")
            if dsh_dir:
                return f"cd {dsh_dir} && npm install @deepseek-ai/dsh@{latest_version}"
            else:
                return f"npm install @deepseek-ai/dsh@{latest_version}"
        elif dsh_type == "pnpm":
            dsh_dir = self.cfg.get("dshDir", "")
            if dsh_dir:
                return f"cd {dsh_dir} && git pull && pnpm install && pnpm run build"
            else:
                return "git pull && pnpm install && pnpm run build"
        elif dsh_type == "manual":
            return "请手动升级：访问 https://github.com/deepseek-ai/dsh/releases"
        else:
            return f"未知安装类型：{dsh_type}"


def build_restart_menu_item(state: str, restart_callback) -> Any:
    """构建「重启以应用新版本」菜单项（票 05）。

    Args:
        state: 当前 DSH 进程状态
        restart_callback: 点击重启时的回调函数

    Returns:
        pystray.MenuItem 或 None（不需要重启时）

    显示条件：
    - 仅在 self-managed 状态（running/starting）时显示
    - external/stopped/crashed/start_failed 不显示
    - 点击才重启，绝不自动重启
    """
    import pystray
    
    # 只在自管进程状态显示
    if state not in ("running", "starting"):
        return None
    
    return pystray.MenuItem(
        "🔄 重启以应用新版本",
        restart_callback,
    )