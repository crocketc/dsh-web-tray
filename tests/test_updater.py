"""updater 模块测试：版本比较、registry 解析、版本查询、当前版本获取。"""
import unittest
from unittest import mock

from tests import new_test_dir

import updater


class TestSemverGt(unittest.TestCase):
    """semver_gt 全矩阵测试：含 prerelease 语义。"""

    def test_prerelease_before_stable(self):
        """0.1.0-rc.7 < 0.1.0"""
        self.assertTrue(updater.semver_gt("0.1.0", "0.1.0-rc.7"))
        self.assertFalse(updater.semver_gt("0.1.0-rc.7", "0.1.0"))

    def test_stable_before_prerelease(self):
        """0.1.0 < 0.1.1-rc.2"""
        self.assertTrue(updater.semver_gt("0.1.1-rc.2", "0.1.0"))
        self.assertFalse(updater.semver_gt("0.1.0", "0.1.1-rc.2"))

    def test_same_version_equal(self):
        """相同版本返回 False（不大于）"""
        self.assertFalse(updater.semver_gt("1.2.3", "1.2.3"))
        self.assertFalse(updater.semver_gt("1.2.3-alpha.1", "1.2.3-alpha.1"))

    def test_major_minor_patch_comparison(self):
        """标准 semver 比较"""
        self.assertTrue(updater.semver_gt("1.2.4", "1.2.3"))
        self.assertTrue(updater.semver_gt("1.3.0", "1.2.9"))
        self.assertTrue(updater.semver_gt("2.0.0", "1.9.9"))
        self.assertFalse(updater.semver_gt("1.2.3", "1.2.4"))

    def test_prerelease_numeric_comparison(self):
        """prerelease 数字段按数值比较"""
        self.assertTrue(updater.semver_gt("1.2.3-rc.2", "1.2.3-rc.1"))
        self.assertFalse(updater.semver_gt("1.2.3-rc.1", "1.2.3-rc.2"))

    def test_prerelease_alpha_comparison(self):
        """prerelease 字母段按 ASCII 比较"""
        self.assertTrue(updater.semver_gt("1.2.3-beta", "1.2.3-alpha"))
        self.assertFalse(updater.semver_gt("1.2.3-alpha", "1.2.3-beta"))

    def test_prerelease_mixed_segments(self):
        """prerelease 多段比较"""
        # 1.2.3-rc.1.beta 比 1.2.3-rc.1 多一段，beta 是字母，所以更大
        self.assertTrue(updater.semver_gt("1.2.3-rc.1.beta", "1.2.3-rc.1"))
        self.assertTrue(updater.semver_gt("1.2.3-rc.2", "1.2.3-rc.1"))

    def test_invalid_version_returns_false(self):
        """非法版本串返回 False"""
        self.assertFalse(updater.semver_gt("", "1.2.3"))
        self.assertFalse(updater.semver_gt("1.2.3", ""))
        self.assertFalse(updater.semver_gt("not-a-version", "1.2.3"))
        self.assertFalse(updater.semver_gt("1.2.3", "not-a-version"))
        self.assertFalse(updater.semver_gt("1.2", "1.2.3"))  # 缺少 patch
        self.assertFalse(updater.semver_gt("1.2.3.4", "1.2.3"))  # 多余段


class TestResolveRegistry(unittest.TestCase):
    """registry 解析三级回退测试。"""

    def test_npm_config_success(self):
        """npm config get registry 成功时使用其值"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="https://registry.npmmirror.com\n",
                returncode=0
            )
            registry = updater._resolve_registry()
            self.assertEqual(registry, "https://registry.npmmirror.com")
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            self.assertEqual(call_args[0][0], ["npm", "config", "get", "registry"])
            self.assertEqual(call_args[1]["timeout"], 5)

    def test_npm_config_fallback_to_npmrc(self):
        """npm config 失败时回退解析 ~/.npmrc"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.Path, "home") as mock_home:
            # npm config 失败
            mock_run.return_value = mock.Mock(returncode=1, stderr="not found")
            # 伪造 ~/.npmrc
            fake_home = new_test_dir()
            mock_home.return_value = fake_home
            npmrc = fake_home / ".npmrc"
            npmrc.write_text("registry=https://custom.registry.com\n", encoding="utf-8")
            
            registry = updater._resolve_registry()
            self.assertEqual(registry, "https://custom.registry.com")

    def test_fallback_to_official(self):
        """都失败时回退到官方 registry"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.Path, "home") as mock_home:
            # npm config 失败
            mock_run.return_value = mock.Mock(returncode=1)
            # .npmrc 不存在或无 registry 行
            fake_home = new_test_dir()
            mock_home.return_value = fake_home
            
            registry = updater._resolve_registry()
            self.assertEqual(registry, "https://registry.npmjs.org")

    def test_npmrc_multiple_registry_lines(self):
        """~/.npmrc 多行时取第一个 registry= 行"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.Path, "home") as mock_home:
            mock_run.return_value = mock.Mock(returncode=1)
            fake_home = new_test_dir()
            mock_home.return_value = fake_home
            npmrc = fake_home / ".npmrc"
            npmrc.write_text(
                "other=value\nregistry=https://first.com\nregistry=https://second.com\n",
                encoding="utf-8"
            )
            
            registry = updater._resolve_registry()
            self.assertEqual(registry, "https://first.com")

    def test_npmrc_commented_registry_ignored(self):
        """~/.npmrc 中注释的 registry= 行应被忽略"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.Path, "home") as mock_home:
            mock_run.return_value = mock.Mock(returncode=1)
            fake_home = new_test_dir()
            mock_home.return_value = fake_home
            npmrc = fake_home / ".npmrc"
            npmrc.write_text(";registry=https://ignored.com\nregistry=https://valid.com\n", encoding="utf-8")
            
            registry = updater._resolve_registry()
            self.assertEqual(registry, "https://valid.com")


class TestFetchLatestVersion(unittest.TestCase):
    """fetch_latest_version HTTP 查询测试。"""

    def test_success(self):
        """成功查询返回版本号"""
        mock_response = mock.Mock()
        mock_response.read.return_value = b'{"version":"1.2.3"}'
        mock_response.getcode.return_value = 200
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=False)
        
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=mock_response) as mock_open:
            version = updater.fetch_latest_version("https://registry.npmjs.org")
            self.assertEqual(version, "1.2.3")
            mock_open.assert_called_once()
            # 验证 URL 编码
            call_url = mock_open.call_args[0][0]
            # URL 编码后 @ 变成 %40
            self.assertIn("%40deepseek-ai%2Fdsh", str(call_url))

    def test_timeout_returns_none(self):
        """超时返回 None"""
        error = updater.urllib.error.URLError("timeout")
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=error):
            version = updater.fetch_latest_version("https://registry.npmjs.org")
            self.assertIsNone(version)

    def test_non_200_returns_none(self):
        """非 200 响应返回 None（通过异常模拟）"""
        mock_response = mock.Mock()
        mock_response.getcode.return_value = 404
        mock_response.read.return_value = b'Not Found'
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=False)
        
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=mock_response):
            version = updater.fetch_latest_version("https://registry.npmjs.org")
            self.assertIsNone(version)

    def test_invalid_json_returns_none(self):
        """坏 JSON 返回 None"""
        mock_response = mock.Mock()
        mock_response.read.return_value = b'not json'
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=False)
        
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=mock_response):
            version = updater.fetch_latest_version("https://registry.npmjs.org")
            self.assertIsNone(version)

    def test_missing_version_field_returns_none(self):
        """JSON 缺少 version 字段返回 None"""
        mock_response = mock.Mock()
        mock_response.read.return_value = b'{"name":"dsh"}'
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=False)
        
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=mock_response):
            version = updater.fetch_latest_version("https://registry.npmjs.org")
            self.assertIsNone(version)


class TestCurrentVersion(unittest.TestCase):
    """current_version 获取当前版本测试。"""

    def test_success(self):
        """成功返回版本号"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.sys, "platform", "darwin"):
            mock_result = mock.Mock()
            mock_result.stdout = "dsh version 1.2.3\n"
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            version = updater.current_version(["/usr/bin/dsh", "--version"])
            self.assertEqual(version, "1.2.3")
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            self.assertEqual(call_args[0][0], ["/usr/bin/dsh", "--version"])
            self.assertEqual(call_args[1]["timeout"], 10)

    def test_non_zero_exit_returns_none(self):
        """非零退出返回 None"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="",
                stderr="command not found",
                returncode=127
            )
            version = updater.current_version(["/usr/bin/dsh", "--version"])
            self.assertIsNone(version)

    def test_no_version_string_returns_none(self):
        """输出无版本串返回 None"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="some output without version",
                returncode=0
            )
            version = updater.current_version(["/usr/bin/dsh", "--version"])
            self.assertIsNone(version)

    def test_windows_path(self):
        """Windows 路径测试"""
        with mock.patch.object(updater.subprocess, "run") as mock_run, \
             mock.patch.object(updater.sys, "platform", "win32"):
            mock_result = mock.Mock()
            mock_result.stdout = "dsh version 2.0.0\n"
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            version = updater.current_version(["C:\\Program Files\\dsh\\dsh.cmd", "--version"])
            self.assertEqual(version, "2.0.0")
            # 验证 Windows CREATE_NO_WINDOW 标志
            call_args = mock_run.call_args
            self.assertIn("creationflags", call_args[1])


class TestCheckForUpdate(unittest.TestCase):
    """check_for_update 判定入口测试。"""

    def test_global_install_with_update(self):
        """global 安装有更新"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value="1.0.0"), \
             mock.patch.object(updater, "fetch_latest_version", return_value="1.1.0"):
            result = updater.check_for_update(cfg)
            self.assertTrue(result["has_update"])
            self.assertEqual(result["current_version"], "1.0.0")
            self.assertEqual(result["latest_version"], "1.1.0")
            self.assertEqual(result["reason"], "")

    def test_global_install_no_update(self):
        """global 安装无更新"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value="1.1.0"), \
             mock.patch.object(updater, "fetch_latest_version", return_value="1.1.0"):
            result = updater.check_for_update(cfg)
            self.assertFalse(result["has_update"])
            self.assertEqual(result["reason"], "已是最新版本")

    def test_global_install_latest_older(self):
        """global 安装 latest 比当前旧（镜像滞后）"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value="1.1.0"), \
             mock.patch.object(updater, "fetch_latest_version", return_value="1.0.0"):
            result = updater.check_for_update(cfg)
            self.assertFalse(result["has_update"])
            self.assertEqual(result["reason"], "已是最新版本")

    def test_local_install_with_update(self):
        """local 安装有更新"""
        cfg = {
            "dshType": "local",
            "dshArgv": ["npx", "@deepseek-ai/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value="1.0.0"), \
             mock.patch.object(updater, "fetch_latest_version", return_value="1.1.0"):
            result = updater.check_for_update(cfg)
            self.assertTrue(result["has_update"])
            self.assertEqual(result["latest_version"], "1.1.0")

    def test_pnpm_install_unknown(self):
        """pnpm 安装返回未知（票 02 处理）"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "/path/to/harness",
        }
        result = updater.check_for_update(cfg)
        self.assertIsNone(result["has_update"])
        self.assertEqual(result["reason"], "未知，跳过判定")

    def test_manual_install_unknown(self):
        """manual 安装返回未知"""
        cfg = {
            "dshType": "manual",
            "dshArgv": ["custom", "command"],
        }
        result = updater.check_for_update(cfg)
        self.assertIsNone(result["has_update"])
        self.assertEqual(result["reason"], "未知，跳过判定")

    def test_fetch_failure(self):
        """查询失败"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value="1.0.0"), \
             mock.patch.object(updater, "fetch_latest_version", return_value=None):
            result = updater.check_for_update(cfg)
            self.assertFalse(result["has_update"])
            self.assertEqual(result["reason"], "检查失败：无法获取最新版本")

    def test_current_version_failure(self):
        """当前版本获取失败"""
        cfg = {
            "dshType": "global",
            "dshArgv": ["/usr/bin/dsh", "web"],
        }
        with mock.patch.object(updater, "current_version", return_value=None), \
             mock.patch.object(updater, "fetch_latest_version", return_value="1.1.0"):
            result = updater.check_for_update(cfg)
            self.assertFalse(result["has_update"])
            self.assertEqual(result["reason"], "检查失败：无法获取当前版本")


if __name__ == "__main__":
    unittest.main()
