import sys
import uuid
from pathlib import Path

# 使 tests 可直接 import 项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试临时目录固定在项目工作区内（.testtmp），普通 mkdir 创建。
# 不用 tempfile.mkdtemp：其限制性 DACL 在部分沙箱环境下会被拒绝写入。
TEST_ROOT = Path(__file__).resolve().parent.parent / ".testtmp"


def new_test_dir() -> Path:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    d = TEST_ROOT / f"t-{uuid.uuid4().hex[:12]}"
    d.mkdir()
    return d
