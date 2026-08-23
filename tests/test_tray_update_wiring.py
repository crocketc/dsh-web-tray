"""Tray update detection wiring tests (ticket 04)."""
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


if __name__ == "__main__":
    unittest.main()
