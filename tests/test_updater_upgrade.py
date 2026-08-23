"""updater 升级命令构造与静默执行测试（票 03）。"""
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import new_test_dir

import updater


class TestBuildUpgradeCommand(unittest.TestCase):
    """build_upgrade_command 四种安装类型命令构造测试。"""

    def test_global_install(self):
        """global 安装：npm install -g @deepseek-ai/dsh@版本"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNotNone(result)
        self.assertEqual(result["argv"], ["npm", "install", "-g", "@deepseek-ai/dsh@1.2.3"])
        self.assertIsNone(result["cwd"])  # global 不需要 cwd
        self.assertIn("npm install -g @deepseek-ai/dsh@1.2.3", result["manual_text"])

    def test_local_install(self):
        """local 安装：在 dshDir 下 npm install @deepseek-ai/dsh@版本"""
        cfg = {
            "dshType": "local",
            "dshArgv": ["npx", "@deepseek-ai/dsh", "web"],
            "dshDir": "/home/user/my-project",
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNotNone(result)
        self.assertEqual(result["argv"], ["npm", "install", "@deepseek-ai/dsh@1.2.3"])
        self.assertEqual(result["cwd"], "/home/user/my-project")
        self.assertIn("npm install @deepseek-ai/dsh@1.2.3", result["manual_text"])
        self.assertIn("/home/user/my-project", result["manual_text"])

    def test_pnpm_install_posix(self):
        """pnpm 源码安装（POSIX）：git pull && pnpm install && pnpm run build"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "/home/user/harness",
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNotNone(result)
        # POSIX 用 shell 串联
        if sys.platform != "win32":
            expected_cmd = "git pull && pnpm install && pnpm run build"
            self.assertEqual(result["argv"], ["/bin/sh", "-c", expected_cmd])
        self.assertEqual(result["cwd"], "/home/user/harness")
        self.assertIn("git pull", result["manual_text"])
        self.assertIn("pnpm install", result["manual_text"])
        self.assertIn("pnpm run build", result["manual_text"])

    def test_pnpm_install_windows(self):
        """pnpm 源码安装（Windows）：用 & 串联命令"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "C:\\Users\\user\\harness",
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNotNone(result)
        if sys.platform == "win32":
            # Windows 用 cmd /c 串联
            expected_cmd = "git pull & pnpm install & pnpm run build"
            self.assertEqual(result["argv"], ["cmd", "/c", expected_cmd])
        self.assertEqual(result["cwd"], "C:\\Users\\user\\harness")

    def test_manual_install_no_command(self):
        """manual 安装：无命令，仅打开发布页 URL"""
        cfg = {
            "dshType": "manual",
            "dshArgv": ["custom", "command"],
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNone(result["argv"])  # 无命令
        self.assertIsNone(result["cwd"])
        self.assertIsNotNone(result["manual_text"])
        self.assertIn("https://github.com/deepseek-ai/dsh/releases", result["manual_text"])

    def test_manual_text_cross_platform(self):
        """手动命令文案跨平台格式化"""
        # Windows 路径
        cfg = {
            "dshType": "global",
            "dshArgv": ["C:\\Program Files\\dsh\\dsh.cmd", "web"],
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIn("npm install -g @deepseek-ai/dsh@1.2.3", result["manual_text"])

    def test_prerelease_version_pinned(self):
        """prerelease 版本号也要 pin"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        result = updater.build_upgrade_command(cfg, "1.2.3-rc.1")
        self.assertIn("@deepseek-ai/dsh@1.2.3-rc.1", result["argv"][3])
        self.assertIn("@deepseek-ai/dsh@1.2.3-rc.1", result["manual_text"])

    def test_local_install_without_dshdir(self):
        """local 安装缺少 dshDir 时回退到当前目录"""
        cfg = {
            "dshType": "local",
            "dshArgv": ["npx", "@deepseek-ai/dsh", "web"],
            "dshDir": "",
        }
        result = updater.build_upgrade_command(cfg, "1.2.3")
        self.assertIsNotNone(result)
        # dshDir 为空时 cwd 为 None（让子进程在当前目录执行）
        self.assertIsNone(result["cwd"])


if __name__ == "__main__":
    unittest.main()
