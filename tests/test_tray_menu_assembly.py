"""Tray menu assembly regression tests.

背景（v1.6.0 打包版启动即退的根因）：_build_menu 引用了 TrayApp 上不存在的方法，
GUI 装配路径是 spec 的第 4 测试缝（不自动化、实机人工验证），单元测试从未走过
_build_menu，导致 AttributeError 只在真实托盘里爆发。本文件用 mock 的 pystray
把整条菜单装配链在单测里跑通，堵住这一类缺陷。
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

import config as cfgmod

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_main_module():
    spec = importlib.util.spec_from_file_location(
        "dsh_web_tray_main", _REPO_ROOT / "dsh-web-tray.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MenuAssemblyTest(unittest.TestCase):
    """公共脚手架：mock pystray + 构造 TrayApp（不进 GUI 主循环）。"""

    def setUp(self):
        self.pystray_patcher = mock.patch.dict(
            "sys.modules", {"pystray": mock.MagicMock()}
        )
        self.pystray_patcher.start()
        self.main = _load_main_module()
        with mock.patch.object(
            self.main.platforms, "is_autostart_enabled", return_value=False
        ):
            self.app = self.main.TrayApp()
        self.app.cfg = cfgmod.default_config()
        self.app.update_mgr = mock.MagicMock()
        self.app.update_mgr.is_upgrade_in_progress.return_value = False

    def tearDown(self):
        self.pystray_patcher.stop()


class TestFullMenuAssembly(_MenuAssemblyTest):
    """整条 _build_menu 装配链不得抛异常（v1.6.0 打包版回归）。"""

    def test_menu_builds_in_plain_state(self):
        """无更新、无升级标记时整条菜单构建成功。"""
        menu = self.app._build_menu()
        self.assertIsNotNone(menu)

    def test_menu_builds_with_upgrade_pending(self):
        """有缓存升级信息 + 升级成功标记时整条菜单构建成功。"""
        self.app.cfg["lastKnownLatestVersion"] = "1.2.4"
        self.app.cfg["_showRestartAfterUpgrade"] = True
        self.app.state = "running"
        menu = self.app._build_menu()
        self.assertIsNotNone(menu)

    def test_menu_builds_while_upgrading(self):
        """升级进行中（置灰态）整条菜单构建成功。"""
        self.app.cfg["lastKnownLatestVersion"] = "1.2.4"
        self.app.update_mgr.is_upgrade_in_progress.return_value = True
        menu = self.app._build_menu()
        self.assertIsNotNone(menu)

    def test_menu_builds_external_state(self):
        """external 态整条菜单构建成功。"""
        self.app.state = "external"
        menu = self.app._build_menu()
        self.assertIsNotNone(menu)


class TestRestartMenuItem(_MenuAssemblyTest):
    """「重启以应用新版本」菜单项的显示条件与点击行为（ADR-0004）。"""

    def test_hidden_without_upgrade_flag(self):
        """默认（未升级）不显示。"""
        self.assertEqual(self.app._build_restart_menu_item(), [])

    def test_hidden_when_flag_but_not_self_managed(self):
        """external/stopped 态即使有标记也不显示。"""
        self.app.cfg["_showRestartAfterUpgrade"] = True
        for state in ("external", "stopped", "crashed", "start_failed"):
            self.app.state = state
            self.assertEqual(self.app._build_restart_menu_item(), [], state)

    def test_shown_after_upgrade_success_running(self):
        """升级成功 + running/starting 时显示。"""
        self.app.cfg["_showRestartAfterUpgrade"] = True
        for state in ("running", "starting"):
            self.app.state = state
            items = self.app._build_restart_menu_item()
            self.assertEqual(len(items), 1, state)


class TestCheckFeedback(_MenuAssemblyTest):
    """检查结果菜单反馈行（通知被拦截时的保底通道）。"""

    def test_feedback_empty_by_default(self):
        self.assertEqual(self.app._last_check_feedback, "")

    def test_feedback_update_found(self):
        self.app._set_check_feedback({
            "has_update": True, "latest_version": "0.1.1-rc.2", "reason": "",
        })
        self.assertIn("有新版本 0.1.1-rc.2", self.app._last_check_feedback)

    def test_feedback_latest(self):
        self.app._set_check_feedback({
            "has_update": False, "current_version": "0.1.0-rc.7", "reason": "已是最新版本",
        })
        self.assertIn("已是最新 0.1.0-rc.7", self.app._last_check_feedback)

    def test_feedback_failure(self):
        self.app._set_check_feedback({
            "has_update": False, "current_version": None,
            "reason": "检查失败：无法获取最新版本",
        })
        self.assertIn("检查失败", self.app._last_check_feedback)
        self.assertIn("无法获取最新版本", self.app._last_check_feedback)
        self.assertNotIn("已是最新", self.app._last_check_feedback)

    def test_feedback_undeterminable(self):
        self.app._set_check_feedback({
            "has_update": None, "reason": "未知，跳过判定",
        })
        self.assertIn("无法判定", self.app._last_check_feedback)

    def test_feedback_row_in_menu(self):
        """反馈行出现在菜单（状态行下方）。"""
        self.app._set_check_feedback({
            "has_update": True, "latest_version": "0.1.1-rc.2", "reason": "",
        })
        menu = self.app._build_menu()
        self.assertIsNotNone(menu)

    def test_feedback_cleared_by_timer(self):
        """30 秒定时器清除反馈。"""
        self.app._set_check_feedback({
            "has_update": False, "current_version": "1.2.3", "reason": "已是最新版本",
        })
        self.assertIsNotNone(self.app._feedback_timer)
        self.app._feedback_timer.cancel()
        self.app._last_check_feedback = ""
        self.assertEqual(self.app._build_menu() is not None, True)


class TestNotificationChannel(_MenuAssemblyTest):
    """通知通道：macOS 原生（UNUserNotificationCenter）与 pystray 回退。"""

    def test_non_darwin_uses_pystray_notify(self):
        """非 macOS 平台走 pystray notify。"""
        self.app.icon = mock.Mock()
        with mock.patch.object(self.main.sys, "platform", "win32"):
            self.app._notify("t", "m")
        self.app.icon.notify.assert_called_once_with("m", "t")

    def test_darwin_notify_missing_framework_falls_back(self):
        """UserNotifications 框架不可用时回退 pystray（不崩溃）。"""
        self.app.icon = mock.Mock()
        with mock.patch.object(self.main.sys, "platform", "darwin"), \
                mock.patch.dict("sys.modules", {"UserNotifications": None}):
            self.app._notify("t", "m")
        self.app.icon.notify.assert_called_once_with("m", "t")

    def test_notify_exception_swallowed(self):
        """通知异常不影响主流程。"""
        self.app.icon = mock.Mock()
        self.app.icon.notify.side_effect = RuntimeError("boom")
        with mock.patch.object(self.main.sys, "platform", "win32"):
            self.app._notify("t", "m")  # 不应抛异常

    def test_no_icon_no_notify(self):
        """icon 未就绪时不发通知。"""
        self.app.icon = None
        self.app._notify("t", "m")

    def test_click_clears_flag_and_restarts(self):
        """点击后：清标记、复用 restart 动作、刷新菜单。"""
        self.app.cfg["_showRestartAfterUpgrade"] = True
        self.app.state = "running"

        sentinel_item = mock.sentinel.item
        with mock.patch.object(
            self.main.update_manager, "build_restart_menu_item",
            return_value=sentinel_item,
        ) as builder:
            items = self.app._build_restart_menu_item()
            self.assertEqual(items, [sentinel_item])
            callback = builder.call_args[0][1]

        with mock.patch.object(self.app, "restart_dsh") as mock_restart, \
                mock.patch.object(self.app, "_refresh_ui") as mock_refresh:
            callback()

        mock_restart.assert_called_once()
        mock_refresh.assert_called_once()
        self.assertNotIn("_showRestartAfterUpgrade", self.app.cfg)
        # 清标记后菜单项消失
        self.assertEqual(self.app._build_restart_menu_item(), [])


if __name__ == "__main__":
    unittest.main()
