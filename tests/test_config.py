"""config 模块测试：argv 数组持久化、端口参数拼装、损坏配置判定。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests import new_test_dir

import config as cfgmod


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.root = new_test_dir()
        patcher = mock.patch.object(cfgmod, "app_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_preserves_argv_with_spaces(self):
        argv = ["C:/deepseek-harness/node_modules/.bin/pnpm.cmd", "dsh", "web"]
        cfg = cfgmod.default_config()
        cfg.update(
            {
                "dshType": "pnpm",
                "dshArgv": argv,
                "dshArgvDisplay": "pnpm dsh web",
                "dshDir": "C:\\deepseek-harness",
                "dshPort": 3080,
            }
        )
        path = cfgmod.save_config(cfg)
        self.assertTrue(path.exists())
        loaded = cfgmod.load_config()
        self.assertIsNotNone(loaded)
        # 关键：argv 数组原样保留（含空格路径不碎裂）
        self.assertEqual(loaded["dshArgv"], argv)
        self.assertEqual(loaded["dshPort"], 3080)

    def test_missing_config_returns_none(self):
        self.assertIsNone(cfgmod.load_config())

    def test_invalid_argv_rejected(self):
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = "pnpm dsh web"  # 字符串而非数组 → 无效
        cfgmod.save_config(cfg)
        self.assertIsNone(cfgmod.load_config())

    def test_invalid_port_rejected(self):
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = ["dsh", "web"]
        cfg["dshPort"] = 70000
        cfgmod.save_config(cfg)
        self.assertIsNone(cfgmod.load_config())

    def test_corrupt_file_returns_none(self):
        cfgmod.config_path().parent.mkdir(parents=True, exist_ok=True)
        cfgmod.config_path().write_text("{ not json", encoding="utf-8")
        self.assertIsNone(cfgmod.load_config())

    def test_bom_config_is_loaded(self):
        """Windows 工具（PowerShell Out-File）写出的 UTF-8 BOM 配置必须能读。"""
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = ["dsh", "web"]
        cfgmod.save_config(cfg)
        raw = cfgmod.config_path().read_bytes()
        cfgmod.config_path().write_bytes(b"\xef\xbb\xbf" + raw)  # 加 BOM
        loaded = cfgmod.load_config()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["dshArgv"], ["dsh", "web"])

    def test_build_argv_with_port(self):
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = ["dsh", "web"]
        cfg["dshPort"] = 3081
        self.assertEqual(cfgmod.build_argv_with_port(cfg), ["dsh", "web", "--port", "3081"])

    def test_build_argv_port_zero_explicit(self):
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = ["dsh", "web"]
        cfg["dshPort"] = 0
        self.assertEqual(cfgmod.build_argv_with_port(cfg), ["dsh", "web", "--port", "0"])

    def test_build_argv_respects_existing_port_flag(self):
        cfg = cfgmod.default_config()
        cfg["dshArgv"] = ["dsh", "web", "--port", "9999"]
        cfg["dshPort"] = 3080
        self.assertEqual(cfgmod.build_argv_with_port(cfg), ["dsh", "web", "--port", "9999"])

    def test_loopback_url(self):
        self.assertEqual(cfgmod.loopback_url_for_port(3080), "http://127.0.0.1:3080")

    def test_self_invocation_script_mode(self):
        inv = cfgmod.self_invocation()
        self.assertEqual(inv[0], sys.executable)
        self.assertTrue(inv[1].endswith("dsh-web-tray.py"))


if __name__ == "__main__":
    unittest.main()
