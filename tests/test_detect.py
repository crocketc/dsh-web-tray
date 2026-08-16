"""detect 模块测试：源码工作区判定、dist 构建检查、候选检测。"""
import json
import unittest
from pathlib import Path

from tests import new_test_dir

import detect


def make_fake_source_root(base: Path, with_dist: bool) -> Path:
    root = base / "fake-harness"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "harness", "scripts": {"dsh": "node apps/cli/src/bin.ts"}}),
        encoding="utf-8",
    )
    if with_dist:
        dist = root / "apps" / "web" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


class TestDetect(unittest.TestCase):
    def setUp(self):
        self.base = new_test_dir()

    def test_is_source_root(self):
        root = make_fake_source_root(self.base, with_dist=False)
        self.assertTrue(detect._is_source_root(root))

    def test_not_source_root_without_workspace(self):
        d = self.base / "plain"
        d.mkdir()
        (d / "package.json").write_text(
            json.dumps({"scripts": {"dsh": "x"}}), encoding="utf-8"
        )
        self.assertFalse(detect._is_source_root(d))

    def test_not_source_root_without_dsh_script(self):
        d = self.base / "other"
        d.mkdir()
        (d / "pnpm-workspace.yaml").write_text("{}", encoding="utf-8")
        (d / "package.json").write_text("{}", encoding="utf-8")
        self.assertFalse(detect._is_source_root(d))

    def test_dist_built_flag(self):
        built = make_fake_source_root(self.base / "a", with_dist=True)
        unbuilt = make_fake_source_root(self.base / "b", with_dist=False)
        self.assertTrue(detect.dist_built(built))
        self.assertFalse(detect.dist_built(unbuilt))

    def test_detect_source_install_via_extra_dirs(self):
        root = make_fake_source_root(self.base, with_dist=True)
        cand = detect.detect_source_install(extra_dirs=[root])
        self.assertIsNotNone(cand)
        self.assertEqual(cand["type"], "pnpm")
        self.assertEqual(cand["command"], ["pnpm", "dsh", "web"])
        self.assertTrue(cand["dist_built"])

    def test_detect_all_includes_real_checkout_if_present(self):
        real = Path("C:/deepseek-harness")
        if not detect._is_source_root(real):
            self.skipTest("本机无 C:/deepseek-harness 源码工作区")
        cands = detect.detect_all()
        types = [c["type"] for c in cands]
        self.assertIn("pnpm", types)

    def test_global_install_on_this_machine(self):
        # CI/其他机器可能没有全局 dsh，仅在有安装时验证
        cand = detect.detect_global_install()
        if cand is None:
            self.skipTest("本机无全局 dsh")
        self.assertEqual(cand["type"], "global")
        self.assertTrue(Path(cand["command"][0]).exists())


class TestVerify(unittest.TestCase):
    def test_verify_rejects_missing_dir(self):
        self.assertFalse(
            detect.verify_installation({"type": "pnpm", "dir": "Z:/nowhere"})
        )


if __name__ == "__main__":
    unittest.main()
