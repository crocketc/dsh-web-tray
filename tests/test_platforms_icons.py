"""platforms 与 trayicons 模块测试。"""
import sys
import unittest


class TestTrayIcons(unittest.TestCase):
    def test_all_states_produce_images(self):
        import trayicons

        for state in list(trayicons.STATE_COLORS) + ["unknown-state"]:
            img = trayicons.make_icon(state)
            self.assertEqual(img.size, (64, 64))
            self.assertEqual(img.mode, "RGBA")

    def test_cache_returns_same_object(self):
        import trayicons

        self.assertIs(trayicons.make_icon("running"), trayicons.make_icon("running"))


class TestPlatformsModule(unittest.TestCase):
    def test_platform_impl_selected(self):
        import platforms

        expected = {
            "win32": "platforms.windows",
            "darwin": "platforms.macos",
        }.get(sys.platform, "platforms.linux")
        self.assertEqual(platforms.platform_impl.__name__, expected)

    def test_is_autostart_enabled_does_not_crash(self):
        import platforms

        platforms.is_autostart_enabled()  # 只读探测


class TestWindowsAutostartRoundTrip(unittest.TestCase):
    def test_roundtrip(self):
        if sys.platform != "win32":
            self.skipTest("Windows 专属")
        import platforms

        inv = [sys.executable, "C:/some path/dsh-web-tray.py"]
        try:
            enabled = platforms.set_autostart(True, inv)
        except OSError:
            self.skipTest("注册表写入被环境拒绝")
        if not enabled:
            self.skipTest("注册表写入被环境拒绝")
        try:
            self.assertTrue(platforms.is_autostart_enabled())
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, platforms.AUTOSTART_NAME)
            # 含空格路径必须带引号（P1 坑）
            self.assertIn('"C:/some path/dsh-web-tray.py"', value)
        finally:
            self.assertTrue(platforms.set_autostart(False, inv))
            self.assertFalse(platforms.is_autostart_enabled())


class TestWizardImportable(unittest.TestCase):
    def test_import(self):
        import wizard  # noqa: F401

        self.assertTrue(callable(wizard.run_wizard))


if __name__ == "__main__":
    unittest.main()
