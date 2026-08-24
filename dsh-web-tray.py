#!/usr/bin/env python3
"""DSH Web Tray — dsh web 的系统托盘守护器（跨平台）。

- 后台启动 dsh web（无终端窗口），stdout 持续排空并解析官方就绪信号
  （``dsh web: http://...`` URL 行），不轮询端口。
- 系统托盘状态：启动中 / 运行中 / 运行中（外部启动）/ 已停止 / 意外退出。
- 退出走 dsh 官方信号契约：POSIX SIGTERM（exit 0，自行清理进程树）；
  Windows 无法投递 SIGTERM，主路径直接 taskkill /T 树杀（见 dsh_process.stop）。
- 崩溃感知：子进程意外退出即切换托盘状态并可一键重启。
- 首次运行/重新配置：向导以子进程运行（macOS 上 tkinter 与 pystray 主线程冲突），
  结果经配置文件回传。

用法：
    python dsh-web-tray.py            # 启动托盘
    python dsh-web-tray.py --wizard   # 直接运行配置向导
    python dsh-web-tray.py --version
    python dsh-web-tray.py --check-update  # 检查更新（诊断）
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# 保证无论从哪个 cwd 启动（双击/资源管理器），同目录模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod
import detect
import platforms
import trayicons
import update_manager
import updater
from dsh_process import DshProcess, port_in_use
from singleinstance import SingleInstance

__version__ = "1.6.3"

APP_NAME = "DSH Web Tray"

#: 就绪等待（秒）：首次启动含 profile bootstrap，可能远超 5 秒
READY_TIMEOUT = 120

log = logging.getLogger("dsh-web-tray")


# --------------------------------------------------------------------------
# PyInstaller --noconsole 下 sys.stdout/stderr 可能为 None：替换为日志流，
# 防止第三方库 print 触发异常。
class _LogStream:
    def __init__(self, level: int) -> None:
        self._level = level

    def write(self, msg: str) -> None:
        if msg and msg.strip():
            log.log(self._level, msg.rstrip())

    def flush(self) -> None:  # pragma: no cover
        pass


def _setup_logging() -> None:
    cfgmod.log_dir().mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(cfgmod.tray_log_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stdout is None:
        sys.stdout = _LogStream(logging.INFO)  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _LogStream(logging.ERROR)  # type: ignore[assignment]


# --------------------------------------------------------------------------
class TrayApp:
    """托盘应用：状态机 + 菜单 + dsh web 子进程编排。"""

    def __init__(self) -> None:
        self.cfg: Optional[dict] = cfgmod.load_config()
        self.icon = None  # pystray.Icon，run() 后可用
        self.state = "stopped"
        self.url: Optional[str] = None
        self.dsh: Optional[DshProcess] = None
        self.exit_code_shown: Optional[int] = None
        self.autostart_on = platforms.is_autostart_enabled()
        self._intentional_stop = False
        self._lifecycle_lock = threading.Lock()
        self.update_mgr: Optional[update_manager.UpdateManager] = None
        self._last_check_feedback = ""  # 检查结果菜单反馈行（通知被拦截时保底）
        self._feedback_timer: Optional[threading.Timer] = None
        self._check_in_progress = False  # 手动检查进行中（防连点并发）

    # ------------------------------------------------------------ 状态与图标
    def _set_state(self, state: str, exit_code: Optional[int] = None) -> None:
        self.state = state
        self.exit_code_shown = exit_code
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        icon = self.icon
        if icon is None:
            return
        if sys.platform == "darwin":
            # macOS 的 pystray 直接操作 AppKit（NSStatusItem），UI 更新必须在主线程。
            # 本方法常从后台线程（就绪/崩溃回调）触发，需派发到主线程执行，
            # 否则图标不刷新（一直停在上一个状态）甚至主线程卡顿（转圈）。
            try:
                import Foundation

                if not Foundation.NSThread.isMainThread():
                    # 投递到主线程 run loop（托盘主线程正阻塞在 icon.run() 的 loop 上）
                    Foundation.NSRunLoop.mainRunLoop().performBlock_(
                        lambda: self._refresh_ui_now(icon)
                    )
                    return
            except Exception:  # pragma: no cover - 派发不可用则降级为直调
                pass
        self._refresh_ui_now(icon)

    def _refresh_ui_now(self, icon) -> None:
        try:
            icon.icon = trayicons.make_icon(self.state)
        except Exception:  # pragma: no cover - 图标后端异常不致命
            pass
        try:
            icon.title = self._tooltip()
        except Exception:  # pragma: no cover
            pass
        try:
            icon.update_menu()
        except Exception:  # pragma: no cover
            pass

    def _status_text(self) -> str:
        if self.state == "starting":
            return "● 启动中…"
        if self.state == "running":
            return f"● 运行中 ({self.url or '…'})"
        if self.state == "external":
            return f"● 运行中（外部启动 {self.url}）"
        if self.state == "crashed":
            return f"✖ 意外退出 (code {self.exit_code_shown})，可重新启动"
        if self.state == "start_failed":
            return "✖ 启动失败，查看日志或重新配置"
        if self.state == "stopping":
            return "○ 正在停止…"
        return "○ 已停止"

    def _tooltip(self) -> str:
        text = f"{APP_NAME} — {self._status_text()}"
        return text[:127]  # Windows tooltip 上限

    # ----------------------------------------------------------------- 编排
    def bootstrap(self) -> None:
        """后台引导：配置缺失则拉向导，然后启动 dsh web。"""
        # 尽早请求通知授权（macOS 首次弹询问；拒绝则回退 pystray）
        self._request_notification_authorization()
        if self.cfg is None:
            log.info("无有效配置，启动配置向导")
            ok = self._run_wizard(install_only=False)
            self.cfg = cfgmod.load_config()
            if not ok or self.cfg is None:
                log.info("向导取消/失败，退出托盘")
                self.quit()
                return
        self.start_dsh()
        self._init_update_manager()

    def _init_update_manager(self) -> None:
        """初始化更新管理器并启动自动检查调度。"""
        if self.cfg is None:
            return
        
        def notify_callback(title: str, message: str) -> None:
            self._notify(title, message)
        
        self.update_mgr = update_manager.UpdateManager(
            self.cfg,
            updater.check_for_update,
            notify_callback,
            save_fn=cfgmod.save_config
        )
        # 启动自动检查调度器（30秒后首次检查，之后每24小时）
        self.update_mgr.start_auto_check_scheduler(delay_seconds=30)

    def start_dsh(self) -> None:
        """启动（或接管显示）dsh web。线程安全：所有状态迁移持锁。"""
        with self._lifecycle_lock:
            if self.dsh and self.dsh.is_running:
                return
            cfg = self.cfg
            if cfg is None:
                return
            port = int(cfg.get("dshPort") or 0)
            if port and port_in_use(port):
                # 场景：终端里已在跑 dsh web → 显示"运行中（外部启动）"，不重复启动
                self.url = cfgmod.loopback_url_for_port(port)
                log.info("端口 %s 已被监听，判定为外部实例：%s", port, self.url)
                self._set_state("external")
                self._notify("DSH Web 已在运行", f"检测到端口 {port} 已有实例（外部启动），托盘仅监控。")
                return
            argv = cfgmod.build_argv_with_port(cfg)
            cwd = cfg.get("dshDir") or str(Path.home())
            if not Path(cwd).is_dir():
                cwd = str(Path.home())
            self._intentional_stop = False
            dsh = DshProcess(argv, cwd, str(cfgmod.dsh_log_path()))
            try:
                dsh.start()
            except OSError as e:
                log.error("启动失败：%s（argv=%s）", e, argv)
                self.dsh = dsh
                self._set_state("start_failed")
                self._notify("启动失败", f"{e}。请通过菜单「重新配置」检查安装。")
                return
            self.dsh = dsh
            self._set_state("starting")
            dsh.watch(self._on_child_exit)
            threading.Thread(target=self._wait_ready_worker, name="dsh-ready", daemon=True).start()

    def _wait_ready_worker(self) -> None:
        dsh = self.dsh
        if dsh is None:
            return
        try:
            url = dsh.wait_ready(timeout=READY_TIMEOUT)
        except RuntimeError as e:
            # 启动即退出：状态迁移由 watch 回调负责，此处只记录
            log.error("dsh web 未就绪：%s", e)
            return
        except TimeoutError as e:
            log.error("dsh web 就绪超时：%s", e)
            if dsh.is_running:
                self._set_state("start_failed")
                self._notify("启动超时", f"{e}")
            return
        self.url = url
        if self.cfg is not None:
            self.cfg["lastUrl"] = url
            try:
                cfgmod.save_config(self.cfg)
            except OSError:
                pass
        log.info("dsh web 就绪：%s", url)
        self._set_state("running")

    def _on_child_exit(self, code: int, had_been_ready: bool) -> None:
        if self._intentional_stop:
            self._set_state("stopped", code)
            return
        if had_been_ready:
            log.warning("dsh web 意外退出（code %s）", code)
            self._set_state("crashed", code)
            self._notify("DSH Web 已退出", f"进程意外退出（code {code}）。可通过托盘菜单重新启动。")
        else:
            log.error("dsh web 启动失败（code %s）", code)
            self._set_state("start_failed", code)
            self._notify(
                "DSH Web 启动失败",
                f"进程启动即退出（code {code}）。日志：{cfgmod.dsh_log_path()}",
            )

    # ------------------------------------------------------------- 子进程控制
    def stop_dsh(self) -> None:
        with self._lifecycle_lock:
            dsh = self.dsh
            if dsh is None or not dsh.is_running:
                self._set_state("stopped")
                return
            self._intentional_stop = True
            self._set_state("stopping")
            # SIGTERM 契约（POSIX）/ taskkill 树杀（Windows）在 DshProcess.stop 内
            dsh.stop(timeout=10)
            self._set_state("stopped", dsh.exit_code)

    def restart_dsh(self) -> None:
        def worker() -> None:
            self.stop_dsh()
            self.start_dsh()

        threading.Thread(target=worker, name="dsh-restart", daemon=True).start()

    # ----------------------------------------------------------------- 向导
    def _run_wizard(self, install_only: bool) -> bool:
        """向导子进程（macOS tkinter/pystray 主线程冲突的解法）。"""
        argv = [sys.executable, "--wizard"] if getattr(sys, "frozen", False) else [
            sys.executable,
            str(Path(__file__).with_name("wizard.py").resolve()),
        ]
        if install_only:
            argv.append("--install-only")
        try:
            result = subprocess.run(argv)
            return result.returncode == 0
        except OSError as e:
            log.error("向导启动失败：%s", e)
            return False

    def reconfigure(self) -> None:
        def worker() -> None:
            self.stop_dsh()
            ok = self._run_wizard(install_only=False)
            fresh = cfgmod.load_config()
            if ok and fresh is not None:
                self.cfg = fresh
                self.autostart_on = platforms.is_autostart_enabled()
                self._refresh_ui()
                self.start_dsh()
            else:
                self._notify("未重新配置", "向导已取消，保持原状。可从菜单手动启动。")

        threading.Thread(target=worker, name="dsh-reconfigure", daemon=True).start()

    # ----------------------------------------------------------------- 菜单动作
    def on_open_browser(self, icon=None, item=None) -> None:
        url = self.url or (self.cfg or {}).get("lastUrl")
        if url:
            platforms.open_url(url)

    def on_restart(self, icon=None, item=None) -> None:
        self.restart_dsh()

    def on_stop(self, icon=None, item=None) -> None:
        def worker() -> None:
            self.stop_dsh()

        threading.Thread(target=worker, daemon=True).start()

    def on_toggle_autostart(self, icon=None, item=None) -> None:
        target = not self.autostart_on
        ok = platforms.set_autostart(target, cfgmod.self_invocation())
        if ok:
            self.autostart_on = target
            if self.cfg is not None:
                self.cfg["autostart"] = target
                try:
                    cfgmod.save_config(self.cfg)
                except OSError:
                    pass
        else:
            self._notify("开机自启", "设置失败，详见托盘日志。")
        self._refresh_ui()

    def on_reconfigure(self, icon=None, item=None) -> None:
        self.reconfigure()

    def on_install_guide(self, icon=None, item=None) -> None:
        def worker() -> None:
            self._run_wizard(install_only=True)
            fresh = cfgmod.load_config()
            if fresh is not None:
                self.cfg = fresh
                if not (self.dsh and self.dsh.is_running):
                    self.start_dsh()

        threading.Thread(target=worker, daemon=True).start()

    def on_check_update(self, icon=None, item=None) -> None:
        """手动检查更新（帮助菜单）。

        反馈策略（macOS 通知常被 ad-hoc 应用拦截，通知只是尽力而为）：
        - 点击后**立即**在菜单里显示「正在检查更新…」，避免毫无反应；
        - 检查结果同步写入菜单反馈行（主菜单顶部 + 帮助子菜单内），
          通知被拦截时用户依然看得到结果；
        - 检查异常时同样把失败写进菜单，不依赖可能被丢弃的通知。
        """
        if self.update_mgr is None:
            self._notify("检查更新", "更新管理器未初始化")
            return

        # 已在检查中：不重复起线程，只刷新进度文案
        if self._check_in_progress:
            self._last_check_feedback = "正在检查更新…"
            self._refresh_ui()
            return

        self._check_in_progress = True
        self._last_check_feedback = "正在检查更新…"
        self._refresh_ui()

        def worker() -> None:
            try:
                result = self.update_mgr.check_now(auto=False)
                # 通知可能被 macOS 拦截（ad-hoc 应用无通知权限）：
                # 结果同步写进菜单（临时反馈行），保证用户一定看得到
                self._set_check_feedback(result)
            except Exception:
                log.exception("手动检查更新失败")
                self._last_check_feedback = "❌ 检查失败（详见日志）"
                self._notify("检查更新", "检查失败，请查看日志")
            finally:
                self._check_in_progress = False
                self._refresh_ui()

        threading.Thread(target=worker, daemon=True, name="manual-check-update").start()

    def _set_check_feedback(self, result: dict) -> None:
        """把检查结果写进菜单临时反馈行（通知被拦截时的保底通道）。

        30 秒后自动清除；再次检查会覆盖。文案与通知保持一致。
        """
        if not isinstance(result, dict):
            self._last_check_feedback = "检查失败"
        elif result.get("has_update"):
            latest = result.get("latest_version") or result.get("reason") or "有新版本"
            self._last_check_feedback = f"🆕 有新版本 {latest}"
        elif result.get("has_update") is None:
            self._last_check_feedback = f"无法判定（{result.get('reason', '未知')}）"
        else:
            reason = result.get("reason", "")
            if reason.startswith("检查失败"):
                detail = reason.split("：", 1)[-1] if "：" in reason else reason
                self._last_check_feedback = f"❌ 检查失败（{detail}）"
            elif result.get("current_version"):
                self._last_check_feedback = f"✓ 已是最新 {result['current_version']}"
            else:
                self._last_check_feedback = "✓ 已是最新"
        
        # 30 秒后自动清除反馈行
        if self._feedback_timer is not None:
            self._feedback_timer.cancel()
        self._feedback_timer = threading.Timer(
            30, lambda: (setattr(self, "_last_check_feedback", ""), self._refresh_ui())
        )
        self._feedback_timer.daemon = True
        self._feedback_timer.start()

    def _build_check_feedback_items(self) -> list:
        """构建检查反馈菜单项（禁用状态，仅展示）。

        出现在两处：主菜单顶部（状态行下方）+ 帮助子菜单「检查更新」正下方
        （用户点击之处），保证通知被拦截时结果依然可见。
        """
        import pystray

        if not self._last_check_feedback:
            return []
        return [
            pystray.MenuItem(
                f"🔍 {self._last_check_feedback}", None, enabled=False
            )
        ]

    def on_docs(self, icon=None, item=None) -> None:
        platforms.open_url(detect.DOCS_URL)

    def on_open_logs(self, icon=None, item=None) -> None:
        cfgmod.log_dir().mkdir(parents=True, exist_ok=True)
        platforms.reveal_path(str(cfgmod.log_dir()))

    def _build_upgrade_menu_items(self) -> list:
        """构建升级菜单项列表。"""
        items = []
        if self.cfg is None or self.update_mgr is None:
            return items
        
        # 检查是否有缓存的更新信息
        latest_version = self.cfg.get("lastKnownLatestVersion", "")
        if not latest_version:
            return items
        
        # 构造虚拟检查结果（用于菜单显示）
        # 源码安装缓存的是 reason 文案（含落后提交数），透传给菜单构造器
        reason = latest_version if "落后" in latest_version else ""
        check_result = {
            "has_update": True,
            "latest_version": latest_version,
            "reason": reason,
        }
        
        # 构建升级菜单项
        upgrade_in_progress = self.update_mgr.is_upgrade_in_progress()
        upgrade_item = update_manager.build_upgrade_menu_item(
            check_result,
            self.cfg,
            self.on_upgrade,
            upgrade_in_progress=upgrade_in_progress,
        )
        
        if upgrade_item is not None:
            items.append(upgrade_item)
        
        return items

    def _build_restart_menu_item(self) -> list:
        """「重启以应用新版本」菜单项（票 05 / ADR-0004）。

        显示条件：升级成功标记（handle_upgrade_result 写入）+ 托盘自管进程
        在跑（external/stopped 等不显示）；点击才重启，绝不自动；
        点击后清除标记（下次菜单构建即隐藏）并复用既有 restart 动作。
        """
        if self.cfg is None:
            return []
        if not self.cfg.get("_showRestartAfterUpgrade"):
            return []
        if self.state not in ("running", "starting"):
            return []

        def on_restart_apply(icon=None, item=None) -> None:
            self.cfg.pop("_showRestartAfterUpgrade", None)
            self.restart_dsh()
            self._refresh_ui()

        item = update_manager.build_restart_menu_item(self.state, on_restart_apply)
        return [item] if item is not None else []

    def on_upgrade(self, icon=None, item=None) -> None:
        """处理升级菜单点击（票 05：接升级结果闭环）。"""
        if self.update_mgr is None or self.cfg is None:
            return
        
        dsh_type = self.cfg.get("dshType", "")
        
        if dsh_type == "manual":
            # manual：打开 DSH 发布页
            platforms.open_url("https://github.com/deepseek-ai/dsh/releases")
        elif dsh_type in ("global", "local", "pnpm"):
            # npm/pnpm：发起静默升级
            if self.update_mgr.is_upgrade_in_progress():
                self._notify("升级", "升级正在进行中，请稍候")
                return
            
            def worker() -> None:
                self.update_mgr.set_upgrade_in_progress(True)
                self._refresh_ui()  # 刷新菜单显示"升级中…"
                
                try:
                    # 调用 ticket 03 的升级执行器
                    cmd = updater.build_upgrade_command(self.cfg, self.cfg.get("lastKnownLatestVersion", "latest"))
                    if cmd is None or cmd.get("argv") is None:
                        log.error("构建升级命令失败")
                        self.update_mgr.handle_upgrade_result(
                            success=False,
                            stderr_tail="升级命令构建失败",
                            state=self.state
                        )
                        return
                    
                    log.info("发起升级：%s", cmd)
                    
                    # 定义升级结果回调（票 05）
                    def on_upgrade_done(exit_code: int, stderr_tail: str) -> None:
                        """升级完成回调（票 05：结果闭环）。"""
                        success = exit_code == 0
                        log.info("升级完成：exit_code=%d, stderr_tail=%s", exit_code, stderr_tail[:100] if stderr_tail else "")
                        # 通知 UpdateManager 处理结果
                        self.update_mgr.handle_upgrade_result(
                            success=success,
                            stderr_tail=stderr_tail,
                            state=self.state
                        )
                        # 刷新菜单（可能显示重启项）
                        self._refresh_ui()
                    
                    # 执行升级（传入回调）
                    updater.execute_upgrade(
                        cmd["argv"],
                        cmd.get("cwd"),
                        str(cfgmod.upgrade_log_path()),
                        on_upgrade_done
                    )
                    
                    log.info("升级已发起，等待回调")
                except Exception as e:
                    log.exception("升级异常")
                    self.update_mgr.handle_upgrade_result(
                        success=False,
                        stderr_tail=f"升级异常: {str(e)}",
                        state=self.state
                    )
                    self._refresh_ui()
            
            threading.Thread(target=worker, daemon=True, name="upgrade-executor").start()

    def quit(self, icon=None, item=None) -> None:
        log.info("退出请求：优雅停止 dsh web…")
        try:
            if self.dsh and self.dsh.is_running:
                self._intentional_stop = True
                self.dsh.stop(timeout=10)
        except Exception:  # pragma: no cover
            log.exception("停止 dsh web 异常")
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # pragma: no cover
                pass

    # ----------------------------------------------------------------- 通知
    def _request_notification_authorization(self) -> None:
        """macOS 首次请求通知授权（UNUserNotificationCenter）。

        pystray 的 macOS 通知走 osascript（macOS 15 上常被静默丢弃），
        原生框架需要用户授权后才可靠送达；失败静默（回退 pystray）。
        """
        if sys.platform != "darwin":
            return
        try:
            from UserNotifications import (
                UNAuthorizationOptionAlert,
                UNAuthorizationOptionSound,
                UNUserNotificationCenter,
            )

            center = UNUserNotificationCenter.currentNotificationCenter()

            def _auth_handler(granted: bool, error) -> None:  # pragma: no cover
                if error is not None:
                    log.warning("通知授权失败：%s", error)
                else:
                    log.info("通知授权：granted=%s", granted)

            center.requestAuthorizationWithOptions_completionHandler_(
                UNAuthorizationOptionAlert | UNAuthorizationOptionSound,
                _auth_handler,
            )
        except Exception:
            log.exception("请求通知授权失败（回退 pystray 通知）")

    def _notify(self, title: str, message: str) -> None:
        log.info("[notify] %s: %s", title, message)
        icon = self.icon
        if icon is None:
            return
        try:
            if sys.platform == "darwin":
                self._notify_macos(title, message)
            else:
                icon.notify(message, title)
        except Exception:  # 通知失败不影响主流程
            pass

    def _notify_macos(self, title: str, message: str) -> None:
        """macOS 原生通知（UNUserNotificationCenter，现代 API）。

        pystray 的 macOS 通知经 osascript 子进程投递，在 macOS 15+ 上
        常被静默丢弃（无授权提示）；原生框架授权后可靠送达、显示应用名。
        任一环节失败回退 pystray。
        """
        try:
            from UserNotifications import (
                UNMutableNotificationContent,
                UNNotificationRequest,
                UNUserNotificationCenter,
            )

            center = UNUserNotificationCenter.currentNotificationCenter()
            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(message)
            request = UNNotificationRequest.requestWithIdentifier_(
                f"dsh-web-tray-{int(time.time() * 1000)}",
                content=content,
                trigger=None,
            )

            def _delivery_handler(error) -> None:  # pragma: no cover
                if error is not None:
                    log.warning("通知投递失败：%s，回退 pystray", error)
                    # ad-hoc 应用 macOS 直接拒绝通知（UNErrorDomain Code=1）：
                    # 异步投递失败时补一次 pystray（osascript）兜底
                    icon = self.icon
                    if icon is not None:
                        try:
                            icon.notify(message, title)
                        except Exception:
                            pass

            center.addNotificationRequest_withCompletionHandler_(
                request, _delivery_handler
            )
        except Exception:
            # 回退 pystray（Windows 走原生；macOS 走 osascript）
            icon = self.icon
            if icon is not None:
                icon.notify(message, title)

    # ----------------------------------------------------------------- 菜单
    def _build_menu(self):
        import pystray

        SEP = getattr(pystray.Menu, "SEPARATOR", None)
        sep = SEP if SEP is not None else pystray.MenuItem("-", None)

        def can_open(item=None) -> bool:
            return bool(self.url or (self.cfg or {}).get("lastUrl"))

        def can_restart(item=None) -> bool:
            return self.state in ("stopped", "crashed", "start_failed", "external")

        def can_stop(item=None) -> bool:
            return self.state in ("running", "starting")

        def autostart_text(item=None) -> str:
            # macOS 上以勾选框呈现（MenuItem checked 参数），文字不带状态
            return "开机自启"

        def autostart_checked(item=None) -> bool:
            return self.autostart_on

        feedback_items = self._build_check_feedback_items()

        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            *feedback_items,
            sep,
            pystray.MenuItem("打开浏览器", self.on_open_browser, enabled=can_open, default=True),
            pystray.MenuItem("重新启动", self.on_restart, enabled=can_restart),
            pystray.MenuItem("停止", self.on_stop, enabled=can_stop),
            pystray.MenuItem(autostart_text, self.on_toggle_autostart, checked=autostart_checked),
            pystray.MenuItem("重新配置", self.on_reconfigure),
            # 升级项（有更新时动态显示）
            *self._build_upgrade_menu_items(),
            # 重启以应用新版本（升级成功后显示，票 05）
            *self._build_restart_menu_item(),
            sep,
            pystray.MenuItem(
                "帮助",
                pystray.Menu(
                    pystray.MenuItem("检查更新", self.on_check_update),
                    # 检查结果反馈行：紧跟「检查更新」，用户点击之处即可见
                    *self._build_check_feedback_items(),
                    pystray.MenuItem("如何安装 DSH", self.on_install_guide),
                    pystray.MenuItem("访问官方文档", self.on_docs),
                    pystray.MenuItem("打开日志目录", self.on_open_logs),
                ),
            ),
            pystray.MenuItem("退出", self.quit),
        )

    # ----------------------------------------------------------------- 主循环
    def run(self) -> int:
        try:
            import pystray
        except ImportError:
            log.error("缺少 pystray：pip install pystray psutil pillow")
            return 2
        self.icon = pystray.Icon(
            APP_NAME,
            icon=trayicons.make_icon("starting"),
            title=self._tooltip(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self.bootstrap, name="dsh-bootstrap", daemon=True).start()
        try:
            self.icon.run()  # 必须主线程（macOS）
        except KeyboardInterrupt:
            self.quit()
        return 0


# --------------------------------------------------------------------------
def _cmd_check_update() -> int:
    """--check-update 入口：打印更新检测结果。"""
    cfg = cfgmod.load_config()
    if cfg is None:
        print("错误：无有效配置，请先运行配置向导")
        return 0
    
    dsh_type = cfg.get("dshType", "")
    print(f"安装类型: {dsh_type}")
    
    if dsh_type == "pnpm":
        # 源码安装：走 git 远端判定（spec 故事 27：CLI 也要给出源码判定结论）
        result = updater.check_for_update(cfg)
        print("当前版本: 未知（源码安装）")
        print("最新版本: 未知（对比 git 远端提交）")
        if result.get("has_update"):
            print(f"结论: {result.get('reason', '有更新')}")
        elif result.get("has_update") is False:
            print("结论: 已是最新（源码与远端一致）")
        else:
            print(f"结论: 未知，跳过判定（{result.get('reason', '')}）")
        return 0
    
    if dsh_type == "manual":
        print("当前版本: 未知")
        print("最新版本: 未知")
        print("结论: 未知，跳过判定")
        return 0
    
    if dsh_type not in ("global", "local"):
        print("当前版本: 未知")
        print("最新版本: 未知")
        print(f"结论: 未知安装类型: {dsh_type}")
        return 0
    
    # global/local：走 registry 检测
    registry = updater._resolve_registry()
    print(f"registry: {registry}")
    result = updater.check_for_update(cfg)
    current = result.get("current_version") or "未知"
    latest = result.get("latest_version") or "未知"
    reason = result.get("reason", "")
    
    print(f"当前版本: {current}")
    print(f"最新版本: {latest}")
    
    if result.get("has_update"):
        print(f"结论: 有新版本 → {latest}")
    else:
        print(f"结论: {reason}")
    
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    
    # 诊断入口不需要日志，先处理
    if "--version" in argv:
        print(f"dsh-web-tray {__version__}")
        return 0
    if "--check-update" in argv:
        return _cmd_check_update()
    
    # 其他入口需要日志
    _setup_logging()

    if "--wizard" in argv:
        import wizard

        return wizard.run_wizard(install_only="--install-only" in argv)

    log.info("===== %s v%s 启动（pid %s）=====", APP_NAME, __version__, os.getpid())
    lock = SingleInstance(cfgmod.app_dir() / "lock")
    if not lock.acquire():
        log.error("已有实例在运行（锁文件 %s），本次退出。", lock.path)
        return 0
    try:
        return TrayApp().run()
    finally:
        lock.release()
        log.info("===== 退出 =====")


if __name__ == "__main__":
    sys.exit(main())