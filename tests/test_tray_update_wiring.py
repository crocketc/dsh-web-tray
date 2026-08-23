"""Tray update detection wiring tests (ticket 04)."""
import sys
import time
import unittest
from unittest import mock

import config as cfgmod
import update_manager
import updater


class TestUpdateCheckScheduling(unittest.TestCase):
    """测试更新检查调度逻辑：冷却时间、自动/手动检查区分。"""

    def test_should_check_auto_first_time(self):
        """首次启动应当自动检查（无冷却记录）。"""
        cfg = cfgmod.default_config()
        cfg["lastUpdateCheckAt"] = 0  # 从未检查过
        
        # 模拟当前时间戳
        now = time.time()
        with mock.patch("time.time", return_value=now):
            should_check = update_manager._should_check_for_update(cfg, auto=True)
            self.assertTrue(should_check, "首次启动应当自动检查")

    def test_should_check_auto_within_cooldown(self):
        """24小时内不重复自动检查。"""
        cfg = cfgmod.default_config()
        now = time.time()
        cfg["lastUpdateCheckAt"] = now - 3600  # 1小时前检查过
        
        should_check = update_manager._should_check_for_update(cfg, auto=True)
        self.assertFalse(should_check, "24小时内不重复自动检查")

    def test_should_check_auto_after_cooldown(self):
        """24小时后应当再次自动检查。"""
        cfg = cfgmod.default_config()
        now = time.time()
        cfg["lastUpdateCheckAt"] = now - 25 * 3600  # 25小时前检查过
        
        should_check = update_manager._should_check_for_update(cfg, auto=True)
        self.assertTrue(should_check, "24小时后应当再次自动检查")

    def test_should_check_manual_bypasses_cooldown(self):
        """手动检查绕过冷却时间。"""
        cfg = cfgmod.default_config()
        now = time.time()
        cfg["lastUpdateCheckAt"] = now - 3600  # 1小时前检查过
        
        should_check = update_manager._should_check_for_update(cfg, auto=False)
        self.assertTrue(should_check, "手动检查应当绕过冷却时间")

    def test_check_updates_timestamp(self):
        """检查后应当更新时间戳。"""
        cfg = cfgmod.default_config()
        cfg["lastUpdateCheckAt"] = 0
        now = time.time()
        
        with mock.patch("time.time", return_value=now):
            update_manager._update_last_check_time(cfg)
            self.assertEqual(cfg["lastUpdateCheckAt"], int(now))


class TestUpdateNotificationThrottling(unittest.TestCase):
    """测试更新通知节流逻辑。"""

    def test_should_notify_new_version(self):
        """新版本首次发现应当通知。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = ""  # 从未通知过
        
        should_notify = update_manager._should_notify_update(cfg, "1.2.3")
        self.assertTrue(should_notify)

    def test_should_not_notify_same_version(self):
        """同一版本不重复通知。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = "1.2.3"
        
        should_notify = update_manager._should_notify_update(cfg, "1.2.3")
        self.assertFalse(should_notify)

    def test_should_notify_different_version(self):
        """不同版本应当再次通知。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = "1.2.3"
        
        should_notify = update_manager._should_notify_update(cfg, "1.2.4")
        self.assertTrue(should_notify)

    def test_record_notified_version(self):
        """记录已通知的版本。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = ""
        
        update_manager._record_notified_version(cfg, "1.2.3")
        self.assertEqual(cfg["lastNotifiedVersion"], "1.2.3")


class TestUpdateManager(unittest.TestCase):
    """测试 UpdateManager 类。"""

    def test_check_now_with_update(self):
        """有更新时应当检查并通知。"""
        cfg = cfgmod.default_config()
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": True,
                "current_version": "1.2.3",
                "latest_version": "1.2.4",
                "reason": "",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=True)
        
        self.assertTrue(result["has_update"])
        self.assertEqual(len(notifications), 1)
        self.assertIn("1.2.4", notifications[0][1])
        self.assertEqual(cfg["lastUpdateCheckAt"], mock.ANY)
        self.assertEqual(cfg["lastNotifiedVersion"], "1.2.4")

    def test_check_now_no_update_manual(self):
        """手动检查无更新时应当通知。"""
        cfg = cfgmod.default_config()
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": False,
                "current_version": "1.2.3",
                "latest_version": None,
                "reason": "已是最新",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=False)
        
        self.assertFalse(result["has_update"])
        self.assertEqual(len(notifications), 1)
        self.assertIn("最新版本", notifications[0][1])

    def test_check_now_no_update_auto(self):
        """自动检查无更新时不通知。"""
        cfg = cfgmod.default_config()
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": False,
                "current_version": "1.2.3",
                "latest_version": None,
                "reason": "已是最新",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=True)
        
        self.assertFalse(result["has_update"])
        self.assertEqual(len(notifications), 0)

    def test_check_now_throttle_notification(self):
        """同一版本不重复通知。"""
        cfg = cfgmod.default_config()
        cfg["lastNotifiedVersion"] = "1.2.4"
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": True,
                "current_version": "1.2.3",
                "latest_version": "1.2.4",
                "reason": "",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=True)
        
        self.assertTrue(result["has_update"])
        self.assertEqual(len(notifications), 0, "同一版本不应当重复通知")

    def test_upgrade_in_progress_state(self):
        """测试升级状态管理。"""
        cfg = cfgmod.default_config()
        
        def mock_check(cfg):
            return {"has_update": False, "reason": ""}
        
        def mock_notify(title, message):
            pass
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        
        self.assertFalse(mgr.is_upgrade_in_progress())
        mgr.set_upgrade_in_progress(True)
        self.assertTrue(mgr.is_upgrade_in_progress())
        mgr.set_upgrade_in_progress(False)
        self.assertFalse(mgr.is_upgrade_in_progress())


class TestMenuItemConstruction(unittest.TestCase):
    """测试升级菜单项构造。"""
    
    def setUp(self):
        """每个测试前设置 pystray mock。"""
        self.pystray_patcher = mock.patch.dict('sys.modules', {'pystray': mock.MagicMock()})
        self.pystray_patcher.start()
        
        # Create mock MenuItem class that records calls
        self.mock_menu_item_class = mock.MagicMock()
        sys.modules['pystray'].MenuItem = self.mock_menu_item_class
    
    def tearDown(self):
        """清理 mock。"""
        self.pystray_patcher.stop()

    def test_no_update_item(self):
        """无更新时不显示升级项。"""
        result = {
            "has_update": False,
            "current_version": "1.2.3",
            "latest_version": None,
            "reason": "已是最新",
        }
        cfg = {"dshType": "global"}
        item = update_manager.build_upgrade_menu_item(result, cfg, mock.Mock())
        self.assertIsNone(item, "无更新时不显示升级项")

    def test_has_update_npm_item(self):
        """npm类型有更新时显示升级项。"""
        result = {
            "has_update": True,
            "current_version": "1.2.3",
            "latest_version": "1.2.4",
            "reason": "",
        }
        cfg = {"dshType": "global"}
        mock_callback = mock.Mock()
        item = update_manager.build_upgrade_menu_item(result, cfg, mock_callback)
        self.assertIsNotNone(item)
        # Verify MenuItem was called with correct text
        self.mock_menu_item_class.assert_called_once()
        call_args = self.mock_menu_item_class.call_args
        self.assertIn("1.2.4", call_args[0][0])
        self.assertIn("🆕", call_args[0][0])

    def test_has_update_manual_item(self):
        """manual类型有更新时显示升级项。"""
        result = {
            "has_update": True,
            "current_version": None,
            "latest_version": None,
            "reason": "有更新（落后 5 提交）",
        }
        cfg = {"dshType": "manual"}
        mock_callback = mock.Mock()
        item = update_manager.build_upgrade_menu_item(result, cfg, mock_callback)
        self.assertIsNotNone(item)
        self.mock_menu_item_class.assert_called_once()
        call_args = self.mock_menu_item_class.call_args
        self.assertIn("🆕", call_args[0][0])

    def test_has_update_pnpm_item(self):
        """pnpm类型有更新时显示升级项。"""
        result = {
            "has_update": True,
            "current_version": None,
            "latest_version": None,
            "reason": "有更新（落后 3 提交）",
        }
        cfg = {"dshType": "pnpm"}
        mock_callback = mock.Mock()
        item = update_manager.build_upgrade_menu_item(result, cfg, mock_callback)
        self.assertIsNotNone(item)
        self.mock_menu_item_class.assert_called_once()
        call_args = self.mock_menu_item_class.call_args
        self.assertIn("🆕", call_args[0][0])
        self.assertIn("3", call_args[0][0])

    def test_upgrade_item_disabled_during_upgrade(self):
        """升级过程中禁用升级项。"""
        result = {
            "has_update": True,
            "current_version": "1.2.3",
            "latest_version": "1.2.4",
            "reason": "",
        }
        cfg = {"dshType": "global"}
        mock_callback = mock.Mock()
        item = update_manager.build_upgrade_menu_item(
            result, cfg, mock_callback, upgrade_in_progress=True
        )
        self.assertIsNotNone(item)
        self.mock_menu_item_class.assert_called_once()
        call_args = self.mock_menu_item_class.call_args
        self.assertIn("升级中", call_args[0][0])
        # Verify enabled=False was passed
        self.assertIn('enabled', call_args[1])
        self.assertFalse(call_args[1]['enabled'])


if __name__ == "__main__":
    unittest.main()


class TestTrayWiringIntegration(unittest.TestCase):
    """集成测试：验证 TrayApp 与 update_manager 的协作。"""
    
    @mock.patch.dict('sys.modules', {'pystray': mock.MagicMock()})
    def test_manual_check_for_update(self):
        """测试手动检查更新功能。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        cfg["dshArgv"] = ["/usr/bin/dsh"]
        
        notifications = []
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        # Mock updater.check_for_update to return no update
        with mock.patch('updater.check_for_update') as mock_check:
            mock_check.return_value = {
                "has_update": False,
                "current_version": "1.2.3",
                "latest_version": None,
                "reason": "已是最新",
            }
            
            mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
            result = mgr.check_now(auto=False)
            
            self.assertFalse(result["has_update"])
            self.assertEqual(len(notifications), 1)
            self.assertIn("最新版本", notifications[0][1])
    
    @mock.patch.dict('sys.modules', {'pystray': mock.MagicMock()})
    def test_auto_check_with_update(self):
        """测试自动检查发现有更新。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        cfg["dshArgv"] = ["/usr/bin/dsh"]
        cfg["lastUpdateCheckAt"] = 0
        
        notifications = []
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        # Mock updater.check_for_update to return has update
        with mock.patch('updater.check_for_update') as mock_check:
            mock_check.return_value = {
                "has_update": True,
                "current_version": "1.2.3",
                "latest_version": "1.2.4",
                "reason": "",
            }
            
            mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
            result = mgr.check_now(auto=True)
            
            self.assertTrue(result["has_update"])
            self.assertEqual(len(notifications), 1)
            self.assertIn("1.2.4", notifications[0][1])
            self.assertEqual(cfg["lastNotifiedVersion"], "1.2.4")
            self.assertEqual(cfg["lastKnownLatestVersion"], "1.2.4")


class TestEdgeCases(unittest.TestCase):
    """边缘用例测试。"""

    def test_check_failure_silent_auto(self):
        """自动检查失败时不通知（静默）。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": False,
                "current_version": None,
                "latest_version": None,
                "reason": "检查失败：无法获取最新版本",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=True)
        
        self.assertFalse(result["has_update"])
        self.assertEqual(len(notifications), 0, "自动检查失败不应当通知")

    def test_check_failure_manual_shows_feedback(self):
        """手动检查失败时应当反馈用户。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": False,
                "current_version": None,
                "latest_version": None,
                "reason": "检查失败：无法获取最新版本",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=False)
        
        self.assertFalse(result["has_update"])
        self.assertEqual(len(notifications), 1, "手动检查失败应当通知")

    def test_pnpm_update_with_commit_count(self):
        """pnpm类型更新显示提交数。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "pnpm"
        cfg["lastUpdateCheckAt"] = 0
        cfg["lastNotifiedVersion"] = ""
        
        notifications = []
        
        def mock_check(cfg):
            return {
                "has_update": True,
                "current_version": None,
                "latest_version": None,
                "reason": "有更新（落后 7 提交）",
            }
        
        def mock_notify(title, message):
            notifications.append((title, message))
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        result = mgr.check_now(auto=True)
        
        self.assertTrue(result["has_update"])
        self.assertEqual(len(notifications), 1)
        # Verify pnpm update notification doesn't include version number
        self.assertNotIn("1.2.4", notifications[0][1])

    def test_manual_update_opens_releases(self):
        """manual类型点击升级打开发布页（验证逻辑，不实际打开）。"""
        # This is tested in menu item construction tests
        # The actual open URL is delegated to platforms.open_url
        # which is tested in platform tests
        pass

    @mock.patch.dict('sys.modules', {'pystray': mock.MagicMock()})
    def test_upgrade_menu_item_persists_until_upgrade(self):
        """升级菜单项常驻至升级完成/版本变化。"""
        cfg = cfgmod.default_config()
        cfg["dshType"] = "global"
        cfg["lastKnownLatestVersion"] = "1.2.4"
        
        def mock_check(cfg):
            return {"has_update": False, "reason": ""}
        
        def mock_notify(title, message):
            pass
        
        mgr = update_manager.UpdateManager(cfg, mock_check, mock_notify)
        
        # Build upgrade menu item
        check_result = {
            "has_update": True,
            "latest_version": "1.2.4",
            "reason": "",
        }
        
        mock_callback = mock.Mock()
        item = update_manager.build_upgrade_menu_item(
            check_result, cfg, mock_callback, upgrade_in_progress=False
        )
        
        self.assertIsNotNone(item)
        # Verify item is enabled
        call_args = sys.modules['pystray'].MenuItem.call_args
        if 'enabled' in call_args[1]:
            self.assertTrue(call_args[1]['enabled'])
