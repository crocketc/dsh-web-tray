"""生成 resources/ 图标资源：icon.png（源图）与 icon.ico（Windows）。

用法：python scripts/generate_icons.py
（macOS 的 icon.icns 需在 macOS 上用 iconutil 生成，见 scripts/build-app.sh）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import trayicons

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def main() -> int:
    RESOURCES.mkdir(parents=True, exist_ok=True)

    # 源图：256×256 运行态风格
    png_path = RESOURCES / "icon.png"
    trayicons.make_icon("running", 256).save(png_path)
    print(f"written {png_path}")

    # Windows .ico：多尺寸
    ico_path = RESOURCES / "icon.ico"
    base = trayicons.make_icon("running", 256)
    base.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"written {ico_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
