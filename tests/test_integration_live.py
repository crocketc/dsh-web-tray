"""真实 dsh web 集成实测（默认跳过，设 DSH_WEB_TRAY_LIVE=1 启用）。

覆盖：启动 → URL 行就绪 → HTTP 可达 → 停止（树杀）→ 无孤儿 → 端口释放，
以及外部实例检测（本机已有 dsh web 在 3080 运行时验证）。
"""
import os
import sys
import unittest
import urllib.request
from pathlib import Path

from tests import new_test_dir

LIVE = os.environ.get("DSH_WEB_TRAY_LIVE") == "1"

from dsh_process import DshProcess, port_in_use, resolve_command
import config as cfgmod


@unittest.skipUnless(LIVE, "设 DSH_WEB_TRAY_LIVE=1 启用真实集成测试")
class TestLiveDshWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resolved = resolve_command(["dsh"])
        if not resolved:
            raise unittest.SkipTest("本机无全局 dsh")
        cls.dsh_cmd = resolved

    def test_full_lifecycle(self):
        tmp = new_test_dir()
        log = str(tmp / "live-dsh.log")
        # 沙箱环境下 ~ 可能不可写：DSH_HOME 重定向到工作区（真实用户机不需要）
        env = {"DSH_HOME": str(tmp / "dsh-home")}
        # --port 0：与正在运行的实例（如 3080）不冲突
        dsh = DshProcess(
            [*self.dsh_cmd, "web", "--port", "0"], str(Path.home()), log, extra_env=env
        )
        dsh.start()
        url = dsh.wait_ready(timeout=120)
        print(f"\n[live] 就绪 URL: {url}")
        self.assertTrue(url.startswith("http://127.0.0.1:"))

        # HTTP 可达（URL 行打印时 API 路由已挂载完毕——官方契约）
        with urllib.request.urlopen(url, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        print("[live] HTTP 200 OK")

        port = int(url.rsplit(":", 1)[1])
        self.assertTrue(port_in_use(port))
        pid = dsh.proc.pid

        dsh.stop(timeout=15)
        print(f"[live] 已停止（exit_code={dsh.exit_code}）")
        self.assertFalse(dsh.is_running)

        # 树杀无孤儿：端口应已释放
        import time

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and port_in_use(port):
            time.sleep(0.5)
        self.assertFalse(port_in_use(port), "停止后端口仍被占用：可能存在孤儿进程")
        print("[live] 端口已释放，无孤儿监听")

        # 日志包含 URL 行（排空线程落盘验证）
        content = Path(log).read_text(encoding="utf-8", errors="replace")
        self.assertIn("dsh web:", content)
        print("[live] 日志已落盘")

    def test_external_instance_detection(self):
        """本机 3080 已有 dsh web（本 GUI 会话）在跑 → 外部启动检测。"""
        import config as c

        if not port_in_use(c.DEFAULT_PORT):
            self.skipTest("本机 3080 无运行中的外部实例")
        self.assertTrue(port_in_use(3080))
        print("[live] 检测到外部实例（外部启动场景成立）")


if __name__ == "__main__":
    unittest.main()
