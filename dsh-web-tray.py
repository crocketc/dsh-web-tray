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
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

# 保证无论从哪个 cwd 启动（双击/资源管理器），同目录模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod
import detect
import platforms
import trayicons
from dsh_process import DshProcess, port_in_use
from singleinstance import SingleInstance

__version__ = "1.5.0"

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

    # ------------------------------------------------------------ 状态与图标
    def _set_state(self, state: str, exit_code: Optional[int] = None) -> None:
        self.state = state
        self.exit_code_shown = exit_code
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        icon = self.icon
        if icon is None:
            return
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
        if self.cfg is None:
            log.info("无有效配置，启动配置向导")
            ok = self._run_wizard(install_only=False)
            self.cfg = cfgmod.load_config()
            if not ok or self.cfg is None:
                log.info("向导取消/失败，退出托盘")
                self.quit()
                return
        self.start_dsh()

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

    def on_docs(self, icon=None, item=None) -> None:
        platforms.open_url(detect.DOCS_URL)

    def on_open_logs(self, icon=None, item=None) -> None:
        cfgmod.log_dir().mkdir(parents=True, exist_ok=True)
        platforms.reveal_path(str(cfgmod.log_dir()))

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
    def _notify(self, title: str, message: str) -> None:
        log.info("[notify] %s: %s", title, message)
        icon = self.icon
        if icon is None:
            return
        try:
            icon.notify(message, title)
        except Exception:  # macOS 后端 NotImplementedError 等
            pass

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
            return f"开机自启（{'已启用' if self.autostart_on else '未启用'}）"

        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            sep,
            pystray.MenuItem("打开浏览器", self.on_open_browser, enabled=can_open, default=True),
            pystray.MenuItem("重新启动", self.on_restart, enabled=can_restart),
            pystray.MenuItem("停止", self.on_stop, enabled=can_stop),
            pystray.MenuItem(autostart_text, self.on_toggle_autostart),
            pystray.MenuItem("重新配置", self.on_reconfigure),
            sep,
            pystray.MenuItem(
                "帮助",
                pystray.Menu(
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
def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _setup_logging()

    if "--version" in argv:
        print(f"dsh-web-tray {__version__}")
        return 0
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
