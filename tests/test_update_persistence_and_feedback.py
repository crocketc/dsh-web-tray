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

    def test_no_update_without_version_no_fake_version(self):
        """pnpm 已最新（无版本号）时不显示假版本「未知」。"""
        cfg = cfgmod.default_config()
        mgr, notifications = _make_mgr(cfg, {
            "has_update": False,
            "current_version": None,
            "latest_version": None,
            "reason": "已是最新",
        }, None)

        mgr.check_now(auto=False)

        self.assertEqual(len(notifications), 1)
        message = notifications[0][1]
        self.assertIn("当前已是最新", message)
        self.assertNotIn("未知", message)

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
        # 节流/缓存键 = reason（含落后数）：计数变化可再次通知、菜单可显示落后数
        self.assertEqual(cfg["lastKnownLatestVersion"], "有更新（落后 3 提交）")
        self.assertEqual(cfg["lastNotifiedVersion"], "有更新（落后 3 提交）")

    def test_pnpm_throttle_differentiates_behind_count(self):
        """落后提交数变化应当再次通知（不是一生只通知一次）。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = "有更新（落后 3 提交）"
        mgr, notifications = _make_mgr(cfg, {
            "has_update": True,
            "current_version": None,
            "latest_version": None,
            "behind_count": 5,
            "reason": "有更新（落后 5 提交）",
        }, None)

        mgr.check_now(auto=True)

        self.assertEqual(len(notifications), 1)
        self.assertIn("落后 5 提交", notifications[0][1])

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

    def test_pnpm_git_determination_printed(self):
        """CLI 对 pnpm 走 git 判定并打印落后提交数（spec 故事 27）。"""
        main_mod = _load_main_module()
        cfg = cfgmod.default_config()
        cfg["dshType"] = "pnpm"
        cfg["dshDir"] = "/fake/harness"

        with mock.patch.object(
            main_mod.cfgmod, "load_config", return_value=cfg
        ), mock.patch.object(
            main_mod.updater, "check_for_update",
            return_value={
                "has_update": True,
                "current_version": None,
                "latest_version": None,
                "behind_count": 4,
                "reason": "有更新（落后 4 提交）",
            },
        ) as mock_check:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main_mod._cmd_check_update()

        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("落后 4 提交", out)
        mock_check.assert_called_once()

    def test_pnpm_git_unknown_printed(self):
        """CLI 对 pnpm git 判定失败时如实打印未知与原因。"""
        main_mod = _load_main_module()
        cfg = cfgmod.default_config()
        cfg["dshType"] = "pnpm"
        cfg["dshDir"] = "/fake/harness"

        with mock.patch.object(
            main_mod.cfgmod, "load_config", return_value=cfg
        ), mock.patch.object(
            main_mod.updater, "check_for_update",
            return_value={
                "has_update": None,
                "current_version": None,
                "latest_version": None,
                "behind_count": 0,
                "reason": "git fetch timeout",
            },
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main_mod._cmd_check_update()

        self.assertEqual(rc, 0)
        self.assertIn("未知，跳过判定", buf.getvalue())
        self.assertIn("git fetch timeout", buf.getvalue())


class TestLocalInstalledVersion(unittest.TestCase):
    """local（npx 本地）安装读 node_modules 包描述取版本（S1 修复）。"""

    def test_reads_package_json_version(self):
        import updater
        from tests import new_test_dir

        tmp = new_test_dir()
        pkg_dir = tmp / "node_modules" / "@deepseek-ai" / "dsh"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            '{"name": "@deepseek-ai/dsh", "version": "0.1.1-rc.2"}',
            encoding="utf-8",
        )

        version = updater.local_installed_version(str(tmp))
        self.assertEqual(version, "0.1.1-rc.2")

    def test_missing_dir_returns_none(self):
        import updater

        self.assertIsNone(updater.local_installed_version(""))
        self.assertIsNone(updater.local_installed_version("/nonexistent/path"))

    def test_bad_json_returns_none(self):
        import updater
        from tests import new_test_dir

        tmp = new_test_dir()
        pkg_dir = tmp / "node_modules" / "@deepseek-ai" / "dsh"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text("not json", encoding="utf-8")

        self.assertIsNone(updater.local_installed_version(str(tmp)))

    def test_check_for_update_local_uses_package_json(self):
        """local 类型检测用包描述版本，不执行 npx --version。"""
        import updater
        from tests import new_test_dir

        tmp = new_test_dir()
        pkg_dir = tmp / "node_modules" / "@deepseek-ai" / "dsh"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            '{"version": "0.1.0"}', encoding="utf-8"
        )
        cfg = {
            "dshType": "local",
            "dshArgv": ["/usr/local/bin/npx", "@deepseek-ai/dsh", "web"],
            "dshDir": str(tmp),
        }

        with mock.patch.object(
            updater, "fetch_latest_version", return_value="0.1.1"
        ), mock.patch.object(
            updater, "current_version"
        ) as mock_cv:
            result = updater.check_for_update(cfg)

        self.assertTrue(result["has_update"])
        self.assertEqual(result["current_version"], "0.1.0")
        self.assertEqual(result["latest_version"], "0.1.1")
        # 绝不能走 npx --version（那是 npm 的版本号）
        mock_cv.assert_not_called()


class TestManualCommandDelegation(unittest.TestCase):
    """失败通知的手动命令与 build_upgrade_command 同源（S6 去重）。"""

    def test_manual_command_matches_builder(self):
        import updater

        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        cfg["lastKnownLatestVersion"] = "1.2.4"
        mgr = update_manager.UpdateManager(cfg, lambda c: {}, lambda t, m: None)

        expected = updater.build_upgrade_command(cfg, "1.2.4")["manual_text"]
        self.assertEqual(mgr._get_manual_command(), expected)
        self.assertIn("@deepseek-ai/dsh@1.2.4", expected)

    def test_pnpm_cached_reason_falls_back_to_latest(self):
        import updater

        cfg = cfgmod.default_config()
        cfg["dshType"] = "pnpm"
        cfg["dshDir"] = "/fake/harness"
        cfg["lastKnownLatestVersion"] = "有更新（落后 3 提交）"
        mgr = update_manager.UpdateManager(cfg, lambda c: {}, lambda t, m: None)

        expected = updater.build_upgrade_command(cfg, "latest")["manual_text"]
        self.assertEqual(mgr._get_manual_command(), expected)


class TestPostUpgradeRecheckBypassesCooldown(unittest.TestCase):
    """升级成功后的重新检查绕过 24h 冷却（S4 修复），保持静默语义。"""

    def test_recheck_forces_and_stays_auto(self):
        cfg = cfgmod.default_config()
        mgr = update_manager.UpdateManager(cfg, lambda c: {}, lambda t, m: None)

        with mock.patch.object(mgr, "check_now") as mock_check:
            mgr.handle_upgrade_result(success=True, stderr_tail="", state="running")

        mock_check.assert_called_once_with(auto=True, force=True)

    def test_force_bypasses_cooldown(self):
        """force=True 时即使刚检查过也执行检查。"""
        cfg = cfgmod.default_config()
        cfg["lastUpdateCheckAt"] = int(__import__("time").time())  # 刚检查过
        mgr, notifications = _make_mgr(cfg, {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新版本",
        }, None)

        result = mgr.check_now(auto=True, force=True)

        self.assertNotEqual(result.get("reason"), "冷却中")
        # auto 语义：无更新不通知
        self.assertEqual(len(notifications), 0)


if __name__ == "__main__":
    unittest.main()
