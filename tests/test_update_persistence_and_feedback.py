"""UpdateManager persistence and feedback tests (ticket 06 drift fixes).

Covers:
- check_now / handle_upgrade_result persist the three state keys via save_fn
  (ADR-0005: cooldown and notification throttle must survive restarts)
- manual-check feedback for undeterminable status (pnpm fetch failure / manual type)
- pnpm update notification text uses reason (behind count), not a fake version
- CLI --check-update prints the resolved registry line (spec story 27)
"""
import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import config as cfgmod
import update_manager

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_main_module():
    spec = importlib.util.spec_from_file_location(
        "dsh_web_tray_main", _REPO_ROOT / "dsh-web-tray.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_mgr(cfg, check_result, save_impl):
    notifications = []

    def mock_check(cfg):
        return check_result

    def mock_notify(title, message):
        notifications.append((title, message))

    mgr = update_manager.UpdateManager(
        cfg, mock_check, mock_notify, save_fn=save_impl
    )
    return mgr, notifications


class TestUpdateManagerPersistence(unittest.TestCase):
    """检查/升级后应持久化状态键（save_fn 被调用且收到同一 cfg）。"""

    def test_check_now_with_update_persists(self):
        cfg = cfgmod.default_config()
        saved = []
        mgr, _ = _make_mgr(cfg, {
            "has_update": True,
            "current_version": "1.2.3",
            "latest_version": "1.2.4",
            "reason": "",
        }, saved.append)

        mgr.check_now(auto=True)

        self.assertEqual(len(saved), 1)
        self.assertIs(saved[0], cfg)
        self.assertEqual(cfg["lastKnownLatestVersion"], "1.2.4")
        self.assertEqual(cfg["lastNotifiedVersion"], "1.2.4")
        self.assertGreater(cfg["lastUpdateCheckAt"], 0)

    def test_check_now_no_update_persists_timestamp(self):
        cfg = cfgmod.default_config()
        saved = []
        mgr, _ = _make_mgr(cfg, {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新版本",
        }, saved.append)

        mgr.check_now(auto=True)

        self.assertEqual(len(saved), 1)
        self.assertGreater(cfg["lastUpdateCheckAt"], 0)

    def test_upgrade_success_persists_cache_clear(self):
        cfg = cfgmod.default_config()
        cfg["lastKnownLatestVersion"] = "1.2.4"
        cfg["lastNotifiedVersion"] = "1.2.4"
        saved = []

        def mock_notify(title, message):
            pass

        mgr = update_manager.UpdateManager(
            cfg, lambda c: {}, mock_notify, save_fn=saved.append
        )
        mgr.handle_upgrade_result(success=True, stderr_tail="", state="running")

        # 成功路径至少保存一次（清缓存后）；随后的异步 recheck 线程可能再存一次
        self.assertGreaterEqual(len(saved), 1)
        self.assertNotIn("lastKnownLatestVersion", cfg)
        self.assertNotIn("lastNotifiedVersion", cfg)

    def test_save_failure_swallowed(self):
        cfg = cfgmod.default_config()

        def bad_save(c):
            raise OSError("disk full")

        mgr, _ = _make_mgr(cfg, {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新版本",
        }, bad_save)

        # 不应抛异常（检查流程不被保存失败打断）
        mgr.check_now(auto=True)

    def test_no_save_fn_still_works(self):
        cfg = cfgmod.default_config()
        mgr, _ = _make_mgr(cfg, {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新版本",
        }, None)
        mgr.check_now(auto=True)
        self.assertGreater(cfg["lastUpdateCheckAt"], 0)


class TestManualCheckFeedback(unittest.TestCase):
    """手动检查的反馈文案：无更新/无法判定要区分（spec 故事 6/7）。"""

    def test_unknown_status_feedback(self):
        """无法判定（has_update=None）时如实说原因，不谎称已最新。"""
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": None,
            "current_version": None,
            "latest_version": None,
            "reason": "未知，跳过判定",
        }, None)

        mgr.check_now(auto=False)

        self.assertEqual(len(notifications), 1)
        title, message = notifications[0]
        self.assertEqual(title, "检查更新")
        self.assertIn("无法判定", message)
        self.assertIn("未知，跳过判定", message)

    def test_no_update_still_latest_message(self):
        """明确无更新时仍显示带版本号的已是最新。"""
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新版本",
        }, None)

        mgr.check_now(auto=False)

        self.assertEqual(len(notifications), 1)
        self.assertIn("当前已是最新版本 1.2.3", notifications[0][1])

    def test_unknown_status_auto_check_silent(self):
        """自动检查遇到无法判定保持静默（只记日志，不通知）。"""
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": None,
            "current_version": None,
            "latest_version": None,
            "reason": "未知，跳过判定",
        }, None)

        mgr.check_now(auto=True)

        self.assertEqual(len(notifications), 0)


class TestPnpmUpdateNotificationText(unittest.TestCase):
    """pnpm 源码有更新时通知用落后提交数，不显示假版本号。"""

    def test_pnpm_notification_uses_reason(self):
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": True,
            "current_version": None,
            "latest_version": None,
            "behind_count": 3,
            "reason": "有更新（落后 3 提交）",
        }, None)

        mgr.check_now(auto=True)

        self.assertEqual(len(notifications), 1)
        title, message = notifications[0]
        self.assertEqual(title, "发现新版本")
        self.assertIn("落后 3 提交", message)
        self.assertNotIn("unknown", message)
        # 缓存与节流键仍写入（菜单升级项依赖 lastKnownLatestVersion）
        self.assertEqual(cfg["lastKnownLatestVersion"], "unknown")
        self.assertEqual(cfg["lastNotifiedVersion"], "unknown")

    def test_npm_notification_uses_version(self):
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": True,
            "current_version": "1.2.3",
            "latest_version": "1.2.4",
            "reason": "",
        }, None)

        mgr.check_now(auto=True)

        self.assertEqual(len(notifications), 1)
        self.assertIn("1.2.4", notifications[0][1])


class TestCliCheckUpdateRegistryLine(unittest.TestCase):
    """CLI --check-update 打印 registry 行（spec 故事 27 诊断需求）。"""

    def test_registry_line_printed_for_npm_types(self):
        main_mod = _load_main_module()
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"

        with mock.patch.object(
            main_mod.cfgmod, "load_config", return_value=cfg
        ), mock.patch.object(
            main_mod.updater, "_resolve_registry",
            return_value="https://registry.example.com"
        ), mock.patch.object(
            main_mod.updater, "check_for_update",
            return_value={
                "has_update": False,
                "current_version": "1.2.3",
                "latest_version": "1.2.3",
                "reason": "已是最新版本",
            },
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main_mod._cmd_check_update()

        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("registry: https://registry.example.com", out)
        self.assertIn("结论:", out)


if __name__ == "__main__":
    unittest.main()
