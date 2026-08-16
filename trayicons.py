"""托盘图标：按运行状态用 PIL 动态生成（打包后无需携带图片资源也能工作）。"""
from __future__ import annotations

from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFont

SIZE = 64

#: 状态 → 主题色
STATE_COLORS: Dict[str, str] = {
    "starting": "#3b82f6",     # 蓝
    "running": "#22c55e",      # 绿
    "external": "#0891b2",     # 青（外部启动）
    "stopping": "#f59e0b",     # 琥珀
    "stopped": "#6b7280",      # 灰
    "crashed": "#ef4444",      # 红
    "start_failed": "#ef4444", # 红
}

_cache: Dict[str, Image.Image] = {}


def _glyph_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # 旧 Pillow 无 size 参数
        return ImageFont.load_default()


def make_icon(state: str, size: int = SIZE) -> Image.Image:
    """生成指定状态的托盘图标（RGBA，含缓存）。"""
    key = f"{state}@{size}"
    if key in _cache:
        return _cache[key]
    color = STATE_COLORS.get(state, STATE_COLORS["stopped"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 2
    # 深色外圈 + 状态色主体 + 高光
    d.ellipse([1, 1, size - 2, size - 2], fill=(15, 23, 42, 255))
    pad = max(3, size // 16)
    d.ellipse([pad, pad, size - pad, size - pad], fill=color)
    if state in ("crashed", "start_failed"):
        # 白色 ✕
        cx = cy = r
        arm = r - pad - max(3, size // 10)
        w = max(3, size // 14)
        d.line([cx - arm, cy - arm, cx + arm, cy + arm], fill="white", width=w)
        d.line([cx - arm, cy + arm, cx + arm, cy - arm], fill="white", width=w)
    elif state == "running":
        # 白色实心点（远看即"在线"）
        dot = max(4, size // 5)
        d.ellipse([r - dot, r - dot, r + dot, r + dot], fill="white")
    elif state == "external":
        # 双环：外部实例
        ring = max(2, size // 16)
        inner = size // 4
        d.ellipse([inner, inner, size - inner, size - inner], outline="white", width=ring)
    elif state == "starting":
        # 白色沙漏状竖条
        w = max(4, size // 10)
        d.rectangle([r - w // 2, pad + w, r + w // 2, size - pad - w], fill="white")
    else:
        # stopped / stopping：空心圆
        ring = max(2, size // 14)
        inner = pad + max(4, size // 8)
        d.ellipse([inner, inner, size - inner, size - inner], outline="white", width=ring)
    _cache[key] = img
    return img


def app_icon() -> Optional[Image.Image]:
    """应用主图标（配置向导等场景）：绿色 running 风格的大图标。"""
    return make_icon("running", 256)
