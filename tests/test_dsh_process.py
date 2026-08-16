"""dsh_process 核心生命周期测试（用 Python 子进程模拟 dsh web）。"""
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests import new_test_dir

from dsh_process import (
    READY_RE,
    DshProcess,
    build_subprocess_env,
    parse_command_line,
    port_in_use,
    resolve_command,
    _resolve_in_common_bin_dirs,
    _resolve_via_login_shell,
)


class TestResolveCommand(unittest.TestCase):
    def _known_command(self) -> str:
        # macOS 没有 python 命令（只有 python3）；Windows 上二者皆可。
        # 用 shutil.which 找一个确定存在的解释器命令名，保证跨平台稳定。
        for name in (sys.executable, "python3", "python"):
            resolved = resolve_command([name])
            if resolved:
                return name
        self.fail("no known command found to test with")

    def test_known_command_resolves_to_absolute(self):
        argv = resolve_command([self._known_command()])
        self.assertIsNotNone(argv)
        self.assertTrue(Path(argv[0]).is_absolute())
        self.assertTrue(Path(argv[0]).exists())

    def test_missing_command_returns_none(self):
        self.assertIsNone(resolve_command(["definitely-not-a-real-cmd-xyz"]))

    def test_extra_args_preserved(self):
        argv = resolve_command([self._known_command(), "-c", "pass"])
        self.assertIsNotNone(argv)
        self.assertEqual(argv[1:], ["-c", "pass"])

    @unittest.skipIf(sys.platform == "win32", "POSIX 兜底仅 macOS/Linux")
    def test_login_shell_fallback_resolves_system_bin(self):
        """受限 PATH（GUI 应用）下，登录 shell 兜底能解析系统命令。"""
        argv = resolve_command(["ls"])
        self.assertIsNotNone(argv)
        self.assertTrue(Path(argv[0]).is_absolute())
        self.assertTrue(Path(argv[0]).exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX 兜底仅 macOS/Linux")
    def test_common_bin_dirs_fallback(self):
        """常见安装目录扫描兜底：在 ~/.local/bin 放一个假命令应能解析。"""
        tmp = new_test_dir()
        bindir = tmp / "bin"
        bindir.mkdir()
        fake = bindir / "dsh-fake-bin"
        fake.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        fake.chmod(0o755)
        self.assertEqual(_resolve_in_common_bin_dirs("definitely-missing"), None)
        # 把扫描目录临时指到测试目录，验证查找逻辑
        import dsh_process

        original = dsh_process._COMMON_BIN_DIRS
        try:
            dsh_process._COMMON_BIN_DIRS = [str(bindir)]
            found = _resolve_in_common_bin_dirs("dsh-fake-bin")
            self.assertEqual(found, str(fake))
        finally:
            dsh_process._COMMON_BIN_DIRS = original

    @unittest.skipIf(sys.platform == "win32", "POSIX 兜底仅 macOS/Linux")
    def test_build_subprocess_env_injects_bin_dirs(self):
        """受限 PATH 下，build_subprocess_env 必须把常见工具目录补进 PATH
        （node 脚本解释器依赖；否则 code 127 'env: node: No such file'）。"""
        tmp = new_test_dir()
        bindir = tmp / "bin"
        bindir.mkdir()
        import dsh_process

        original = dsh_process._COMMON_BIN_DIRS
        old_path = os.environ.get("PATH", "")
        try:
            dsh_process._COMMON_BIN_DIRS = [str(bindir)]
            os.environ["PATH"] = "/usr/bin:/bin"
            env = build_subprocess_env()
            self.assertIn(str(bindir), env["PATH"].split(":"))
            self.assertIn("/usr/bin", env["PATH"].split(":"))  # 原 PATH 保留
            # extra_env 仍能覆盖
            env2 = build_subprocess_env({"FOO": "bar"})
            self.assertEqual(env2["FOO"], "bar")
        finally:
            dsh_process._COMMON_BIN_DIRS = original
            os.environ["PATH"] = old_path


class TestParseCommandLine(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_command_line("dsh web"), ["dsh", "web"])

    def test_quoted_path_with_spaces(self):
        argv = parse_command_line('"C:\\Program Files\\tool\\x.exe" web --port 3080')
        self.assertEqual(argv, ["C:\\Program Files\\tool\\x.exe", "web", "--port", "3080"])

    def test_empty(self):
        self.assertEqual(parse_command_line("   "), [])


class TestPortInUse(unittest.TestCase):
    def test_detects_listener(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(port_in_use(port))
        finally:
            srv.close()

    def test_free_port(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        time.sleep(0.1)
        self.assertFalse(port_in_use(port))

    def test_port_zero_skipped(self):
        self.assertFalse(port_in_use(0))


class TestReadyRegex(unittest.TestCase):
    def test_basic_url_line(self):
        m = READY_RE.search("some log row\ndsh web: http://127.0.0.1:3080\n")
        self.assertEqual(m.group(1), "http://127.0.0.1:3080")

    def test_url_line_with_lan_suffix(self):
        line = "dsh web: http://127.0.0.1:3080 (LAN: http://192.168.1.5:3080)\n"
        m = READY_RE.search(line)
        self.assertEqual(m.group(1), "http://127.0.0.1:3080")


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = new_test_dir()
        self.log = str(self.tmp / "fake-dsh.log")

    def _spawn(self, code: str) -> DshProcess:
        argv = [sys.executable, "-c", code]
        dsh = DshProcess(argv, str(self.tmp), self.log)
        dsh.start()
        return dsh

    def test_start_ready_stop(self):
        dsh = self._spawn(
            "import time; print('dsh web: http://127.0.0.1:39999', flush=True); time.sleep(60)"
        )
        self.addCleanup(dsh.stop)
        url = dsh.wait_ready(timeout=15)
        self.assertEqual(url, "http://127.0.0.1:39999")
        self.assertTrue(dsh.is_running)
        dsh.stop(timeout=10)
        self.assertFalse(dsh.is_running)

    def test_stdout_drained_to_log(self):
        dsh = self._spawn(
            "print('line-1', flush=True); print('dsh web: http://127.0.0.1:39998', flush=True); "
            "import time; time.sleep(60)"
        )
        self.addCleanup(dsh.stop)
        dsh.wait_ready(timeout=15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            content = Path(self.log).read_text(encoding="utf-8")
            if "line-1" in content and "dsh web:" in content:
                break
            time.sleep(0.1)
        content = Path(self.log).read_text(encoding="utf-8")
        self.assertIn("line-1", content)
        self.assertIn("dsh web: http://127.0.0.1:39998", content)

    def test_ready_even_when_log_dir_unwritable(self):
        """日志打开失败绝不能中断排空（P0：PIPE 不读会阻塞子进程）。"""
        dsh = DshProcess(
            [sys.executable, "-c",
             "print('dsh web: http://127.0.0.1:39996', flush=True); import time; time.sleep(60)"],
            str(self.tmp),
            str(self.tmp / "no-such-parent" / "sub" / "x.log"),
        )
        # 构造不可写日志：用一个"只读占位目录"当 log path 的父级
        ro_dir = self.tmp / "readonly-dir"
        ro_dir.mkdir()
        (ro_dir / "blocker").write_text("occupied", encoding="utf-8")
        dsh.log_path = str(ro_dir / "blocker" / "deeper" / "log")
        dsh.start()
        self.addCleanup(dsh.stop)
        url = dsh.wait_ready(timeout=15)
        self.assertEqual(url, "http://127.0.0.1:39996")

    def test_immediate_exit_raises_start_failed(self):
        dsh = self._spawn("import sys; print('boom', flush=True); sys.exit(3)")
        with self.assertRaises(RuntimeError) as ctx:
            dsh.wait_ready(timeout=15)
        self.assertIn("code 3", str(ctx.exception))
        self.assertFalse(dsh.is_running)

    def test_timeout_when_no_url_line(self):
        dsh = self._spawn("import time; time.sleep(60)")
        self.addCleanup(dsh.stop)
        with self.assertRaises(TimeoutError):
            dsh.wait_ready(timeout=2)

    def test_watch_reports_crash(self):
        events = []
        dsh = self._spawn(
            "import time; print('dsh web: http://127.0.0.1:39997', flush=True); time.sleep(60)"
        )
        dsh.wait_ready(timeout=15)
        dsh.watch(lambda code, ready: events.append((code, ready)))
        # 模拟意外崩溃：外部硬杀（不经 stop 的 intentional 标记）。
        # 用 psutil 进程内 kill——外部 taskkill 在部分受限环境被拒绝。
        if sys.platform == "win32":
            import psutil

            psutil.Process(dsh.proc.pid).kill()
        else:
            import signal

            dsh.proc.send_signal(signal.SIGKILL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not events:
            time.sleep(0.1)
        self.assertTrue(events)
        code, had_ready = events[0]
        self.assertNotEqual(code, 0)
        self.assertTrue(had_ready)

    def test_stop_on_dead_process_is_noop(self):
        dsh = self._spawn("import sys; sys.exit(0)")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and dsh.proc.poll() is None:
            time.sleep(0.1)
        dsh.stop()  # 不应抛异常
        self.assertIsNotNone(dsh.proc.poll())


if __name__ == "__main__":
    unittest.main()
