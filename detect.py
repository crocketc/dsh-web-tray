"""dsh 安装检测（源码 / npm 全局 / npm 本地）+ 构建前置检查。

统一原则：检测通过后立刻 resolve_command() 解析出绝对路径 argv 存入配置——
运行期（尤其 macOS GUI 环境）不再依赖 PATH。

源码安装的构建前置：前端 dist 未构建时 dsh web 启动即抛
"web-app: frontend dist not built; run pnpm run build from the repository root first"
（packages/bundle/web-app/src/index.ts），dist 位于 apps/web/dist。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsh_process import IS_WINDOWS, build_subprocess_env, resolve_command

DSH_NPM_PACKAGE = "@deepseek-ai/dsh"
DOCS_URL = "https://github.com/deepseek-ai/harness"

INSTALL_COMMANDS = {
    "global": "npm install -g @deepseek-ai/dsh",
    "source": (
        "git clone https://github.com/deepseek-ai/harness.git && "
        "cd harness && pnpm install && pnpm run build"
    ),
}

#: 源码安装的常见额外扫描位置（cwd / 脚本目录向上找不到时）
COMMON_SOURCE_DIRS = [
    Path.home() / "deepseek-harness",
    Path.home() / "harness",
    Path.home() / "dsh",
    Path.home() / "code" / "deepseek-harness",
    Path.home() / "dev" / "deepseek-harness",
    Path.home() / "Developer" / "deepseek-harness",
    Path.home() / "Documents" / "deepseek-harness",
    Path("C:/deepseek-harness"),
]


def _is_source_root(directory: Path) -> bool:
    """判定某目录是否 dsh 源码工作区根（pnpm workspace + 根 package.json 的 dsh 脚本）。"""
    if not (directory / "pnpm-workspace.yaml").is_file():
        return False
    pkg = directory / "package.json"
    if not pkg.is_file():
        return False
    try:
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return "dsh" in (data.get("scripts") or {})


def dist_built(source_root: Path) -> bool:
    """源码安装的前端 dist 构建检查（未构建则启动必失败）。"""
    return (source_root / "apps" / "web" / "dist" / "index.html").is_file()


def _walk_up_roots() -> List[Path]:
    roots: List[Path] = []
    seen = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for node in [start, *start.parents]:
            if node in seen:
                continue
            seen.add(node)
            roots.append(node)
    return roots


def detect_source_install(extra_dirs: Optional[List[Path]] = None) -> Optional[Dict[str, Any]]:
    """源码安装检测：向上查找 pnpm-workspace.yaml + dsh 脚本；附 dist 构建检查。"""
    candidates = _walk_up_roots() + [Path(p) for p in (extra_dirs or COMMON_SOURCE_DIRS) if p]
    for node in candidates:
        if _is_source_root(node):
            return {
                "type": "pnpm",
                "dir": str(node),
                "command": ["pnpm", "dsh", "web"],
                "display": "pnpm dsh web",
                "dist_built": dist_built(node),
                "build_hint": "pnpm install && pnpm run build",
            }
    return None


def detect_global_install() -> Optional[Dict[str, Any]]:
    """npm 全局安装检测：PATH 上的 dsh（含登录 shell 兜底）。"""
    resolved = resolve_command(["dsh"])
    if resolved:
        return {
            "type": "global",
            "dir": "",
            "command": [resolved[0], "web"],
            "display": "dsh web",
            "dist_built": True,
            "build_hint": "",
        }
    return None


def detect_local_install(base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """npm 本地安装检测：node_modules/@deepseek-ai/dsh。"""
    bases = [Path(base_dir)] if base_dir else [Path.cwd(), Path(__file__).resolve().parent]
    for base in bases:
        pkg = base / "node_modules" / DSH_NPM_PACKAGE
        bin_js = pkg / "lib" / "bin.js"
        if pkg.is_dir() and bin_js.is_file():
            node = resolve_command(["node"])
            if node:
                return {
                    "type": "local",
                    "dir": str(base),
                    "command": ["npx", DSH_NPM_PACKAGE, "web"],
                    "display": f"npx {DSH_NPM_PACKAGE} web",
                    "dist_built": True,
                    "build_hint": "",
                }
    return None


def detect_all(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """按优先级检测：源码 → 全局 → 本地。返回候选列表（可能为空）。"""
    found: List[Dict[str, Any]] = []
    for fn in (detect_source_install, detect_global_install, lambda: detect_local_install(base_dir)):
        cand = fn()
        if cand:
            found.append(cand)
    return found


def verify_installation(candidate: Dict[str, Any]) -> bool:
    """验证候选安装可用（含源码安装的构建前置）。"""
    try:
        ctype = candidate.get("type")
        if ctype == "pnpm":
            root = Path(candidate.get("dir") or "")
            if not _is_source_root(root):
                return False
            return dist_built(root)
        if ctype in ("global", "local"):
            argv = resolve_command(candidate.get("command") or [])
            if not argv:
                return False
            # macOS GUI 场景 PATH 受限：node 脚本（pnpm/dsh）需要解释器，
            # 必须用增强 PATH 验证，否则 code 127 误判为未安装。
            result = subprocess.run(
                [argv[0], "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0,
                env=build_subprocess_env(),
            )
            return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
    return False


def resolve_candidate_argv(command: List[str]) -> Optional[List[str]]:
    """把候选的短命令解析为绝对路径 argv（存入配置，运行期不再依赖 PATH）。"""
    return resolve_command(command)
