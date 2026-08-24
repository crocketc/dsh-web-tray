"""updater 模块测试：版本比较、registry 解析、版本查询、当前版本获取。"""
import unittest
import subprocess
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
            "dshDir": "/home/user/my-project",
        }
        with mock.patch.object(updater, "local_installed_version", return_value="1.0.0"), \
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


class TestGitDetection(unittest.TestCase):
    """git 远端检测测试（pnpm 源码安装）。"""

    def test_git_has_remote_updates(self):
        """远端有 3 个新提交"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            # git fetch 成功（无输出）
            fetch_result = mock.Mock()
            fetch_result.returncode = 0
            fetch_result.stdout = b""
            fetch_result.stderr = b""
            
            # git symbolic-ref 成功
            symbolic_result = mock.Mock()
            symbolic_result.returncode = 0
            symbolic_result.stdout = b"main\n"
            symbolic_result.stderr = b""
            
            # git rev-list count 显示落后 3 个提交
            count_result = mock.Mock()
            count_result.returncode = 0
            count_result.stdout = b"3"
            
            mock_run.side_effect = [fetch_result, symbolic_result, count_result]
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["behind_count"], 3)
            self.assertEqual(result["status"], "behind")

    def test_git_no_remote_updates(self):
        """远端无新提交（持平）"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            fetch_result = mock.Mock()
            fetch_result.returncode = 0
            fetch_result.stdout = b""
            fetch_result.stderr = b""
            
            symbolic_result = mock.Mock()
            symbolic_result.returncode = 0
            symbolic_result.stdout = b"main\n"
            symbolic_result.stderr = b""
            
            count_result = mock.Mock()
            count_result.returncode = 0
            count_result.stdout = b"0"
            
            mock_run.side_effect = [fetch_result, symbolic_result, count_result]
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["behind_count"], 0)
            self.assertEqual(result["status"], "current")

    def test_git_fetch_timeout(self):
        """git fetch 超时"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            mock_run.side_effect = updater.subprocess.TimeoutExpired("git", 5)
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["status"], "unknown")
            self.assertIn("timeout", result["reason"].lower())

    def test_git_not_a_repository(self):
        """不是 git 仓库"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            error = updater.subprocess.CalledProcessError(
                128, ["git", "fetch"], stderr=b"fatal: not a git repository"
            )
            mock_run.side_effect = error
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["status"], "unknown")
            self.assertIn("not a git repository", result["reason"].lower())

    def test_git_no_remote(self):
        """无远端配置"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            error = updater.subprocess.CalledProcessError(
                128, ["git", "fetch"], stderr=b"fatal: 'origin' does not appear to be a git repository"
            )
            mock_run.side_effect = error
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["status"], "unknown")
            self.assertIn("no remote configured", result["reason"].lower())

    def test_git_detached_head(self):
        """detached HEAD 状态"""
        with mock.patch.object(updater.subprocess, "run") as mock_run:
            # fetch 成功
            fetch_result = mock.Mock()
            fetch_result.returncode = 0
            fetch_result.stdout = b""
            fetch_result.stderr = b""
            
            # git symbolic-ref 失败（detached HEAD）
            error = updater.subprocess.CalledProcessError(
                128, ["git", "symbolic-ref"], stderr=b"fatal: ref HEAD is not a symbolic ref"
            )
            
            mock_run.side_effect = [fetch_result, error]
            
            result = updater._fetch_git_remote("/fake/repo", "origin/main")
            self.assertEqual(result["status"], "unknown")
            self.assertIn("detached head", result["reason"].lower())


class TestPnpmUpdateDetection(unittest.TestCase):
    """pnpm 源码安装更新检测集成测试。"""

    def test_pnpm_with_updates(self):
        """pnpm 安装有更新（落后 2 提交）"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "/fake/harness",
        }
        with mock.patch.object(updater, "_fetch_git_remote") as mock_fetch:
            mock_fetch.return_value = {
                "status": "behind",
                "behind_count": 2,
                "reason": ""
            }
            
            result = updater.check_for_update(cfg)
            self.assertTrue(result["has_update"])
            self.assertEqual(result["behind_count"], 2)
            self.assertEqual(result["reason"], "有更新（落后 2 提交）")

    def test_pnpm_no_updates(self):
        """pnpm 安装已是最新"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "/fake/harness",
        }
        with mock.patch.object(updater, "_fetch_git_remote") as mock_fetch:
            mock_fetch.return_value = {
                "status": "current",
                "behind_count": 0,
                "reason": ""
            }
            
            result = updater.check_for_update(cfg)
            self.assertFalse(result["has_update"])
            self.assertEqual(result["behind_count"], 0)
            self.assertEqual(result["reason"], "已是最新")

    def test_pnpm_unknown_git_state(self):
        """pnpm 安装 git 状态未知"""
        cfg = {
            "dshType": "pnpm",
            "dshArgv": ["pnpm", "dsh", "web"],
            "dshDir": "/fake/harness",
        }
        with mock.patch.object(updater, "_fetch_git_remote") as mock_fetch:
            mock_fetch.return_value = {
                "status": "unknown",
                "behind_count": 0,
                "reason": "not a git repository"
            }
            
            result = updater.check_for_update(cfg)
            self.assertIsNone(result["has_update"])
            self.assertEqual(result["reason"], "未知，跳过判定")

class TestGitDetectionIntegration(unittest.TestCase):
    """git 远端检测集成测试（真实 git 仓库，file:// 协议）。"""

    def setUp(self):
        """创建临时 bare 仓库和 clone 仓库"""
        self.test_dir = new_test_dir()
        
        # 1. 创建 bare 仓库（远端）
        self.bare_repo = self.test_dir / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(self.bare_repo)],
            capture_output=True,
            check=True
        )
        
        # 2. clone 仓库（本地工作区）
        self.work_repo = self.test_dir / "work"
        subprocess.run(
            ["git", "clone", str(self.bare_repo), str(self.work_repo)],
            capture_output=True,
            check=True
        )
        
        # 3. 在本地仓库创建初始提交
        readme = self.work_repo / "README.md"
        readme.write_text("# Test Repository\n", encoding="utf-8")
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(self.work_repo),
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=str(self.work_repo),
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "add", "README.md"],
            cwd=str(self.work_repo),
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=str(self.work_repo),
            capture_output=True,
            check=True
        )
        
        # 确定默认分支名称（main 或 master）
        branch_result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(self.work_repo),
            capture_output=True,
            text=True,
            check=True
        )
        self.default_branch = branch_result.stdout.strip()
        
        subprocess.run(
            ["git", "push", "origin", self.default_branch],
            cwd=str(self.work_repo),
            capture_output=True,
            check=True
        )

    def test_integration_no_updates(self):
        """集成测试：无更新（本地和远端相同）"""
        result = updater._fetch_git_remote(str(self.work_repo), f"origin/{self.default_branch}")
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["behind_count"], 0)

    def test_integration_with_updates(self):
        """集成测试：远端有新提交"""
        # 在 bare 仓库直接添加 3 个新提交
        for i in range(1, 4):
            # 临时 clone，提交，push
            temp_clone = self.test_dir / f"temp{i}"
            subprocess.run(
                ["git", "clone", str(self.bare_repo), str(temp_clone)],
                capture_output=True,
                check=True
            )
            
            # 添加新文件
            new_file = temp_clone / f"file{i}.txt"
            new_file.write_text(f"Content {i}\n", encoding="utf-8")
            
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=str(temp_clone),
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=str(temp_clone),
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "add", f"file{i}.txt"],
                cwd=str(temp_clone),
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"Commit {i}"],
                cwd=str(temp_clone),
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "push", "origin", self.default_branch],
                cwd=str(temp_clone),
                capture_output=True,
                check=True
            )
        
        # 现在工作区应该落后 3 个提交
        result = updater._fetch_git_remote(str(self.work_repo), f"origin/{self.default_branch}")
        self.assertEqual(result["status"], "behind")
        self.assertEqual(result["behind_count"], 3)

    def test_integration_non_git_directory(self):
        """集成测试：非 git 目录"""
        non_git_dir = self.test_dir / "not_git"
        non_git_dir.mkdir()
        
        result = updater._fetch_git_remote(str(non_git_dir), "origin/main")
        # 非 git 目录应该返回 unknown（超时或失败）或在某些配置下返回 current
        # 重点是不会误报为 "behind"（不会提示有更新）
        self.assertNotEqual(result["status"], "behind", 
                           "Non-git directory should never report as behind")

    def test_integration_no_remote(self):
        """集成测试：无远端的仓库"""
        no_remote_repo = self.test_dir / "no_remote"
        subprocess.run(
            ["git", "init", str(no_remote_repo)],
            capture_output=True,
            check=True
        )
        
        result = updater._fetch_git_remote(str(no_remote_repo), "origin/main")
        self.assertEqual(result["status"], "unknown")
        # 无 origin 远端时，git fetch 会报错
        self.assertIn("no remote configured", result["reason"].lower())