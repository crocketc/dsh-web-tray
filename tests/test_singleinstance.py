"""单实例锁测试：互斥、陈旧锁回收、释放后再获取。"""
import json
import os
import unittest

from tests import new_test_dir

from singleinstance import SingleInstance


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self.lock_path = new_test_dir() / "lock"

    def test_acquire_and_release(self):
        a = SingleInstance(self.lock_path)
        self.assertTrue(a.acquire())
        self.assertTrue(self.lock_path.exists())
        a.release()
        self.assertFalse(self.lock_path.exists())

    def test_second_acquire_fails_while_held(self):
        a = SingleInstance(self.lock_path)
        self.assertTrue(a.acquire())
        b = SingleInstance(self.lock_path)
        self.assertFalse(b.acquire())  # pid=本进程且存活 → 拒绝
        a.release()

    def test_acquire_after_release(self):
        a = SingleInstance(self.lock_path)
        self.assertTrue(a.acquire())
        a.release()
        b = SingleInstance(self.lock_path)
        self.assertTrue(b.acquire())
        b.release()

    def test_stale_lock_from_dead_pid_is_reclaimed(self):
        # pid 0x7FFFFFFF 上基本不可能有存活进程；create_time 任意
        self.lock_path.write_text('{"pid": 2147483000, "create_time": 1.0}', encoding="utf-8")
        a = SingleInstance(self.lock_path)
        self.assertTrue(a.acquire())
        a.release()

    def test_pid_reuse_guard(self):
        # 同一 pid 但 create_time 不吻合（陈旧锁）→ 回收
        self.lock_path.write_text(
            json.dumps({"pid": os.getpid(), "create_time": 1000.0}), encoding="utf-8"
        )
        a = SingleInstance(self.lock_path)
        self.assertTrue(a.acquire())
        a.release()


if __name__ == "__main__":
    unittest.main()
