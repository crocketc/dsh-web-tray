"""updater：dsh 版本更新检测核心（npm 类安装）。

本模块承载更新检测的纯函数逻辑，不依赖托盘 UI：
- semver_gt：手写 semver 比较（含 prerelease）
- _resolve_registry：三级回退解析 npm registry
- fetch_latest_version：HTTP GET registry latest（stdlib urllib）
- current_version：执行 dsh --version 获取当前版本
- check_for_update：判定入口，聚合上述函数

设计原则（ADR-0001）：
- 不 shell 出 npm CLI（EPERM 教训）
- 尊重用户 registry 配置
- 超时静默返回 None，不抛异常
- 镜像滞后（latest < 当前）不提示升级
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# 包名常量
DSH_NPM_PACKAGE = "@deepseek-ai/dsh"
# 官方 registry（最终回退）
OFFICIAL_REGISTRY = "https://registry.npmjs.org"

# semver 正则（含 prerelease）
# 不用 ^/$ 锚点，以匹配输出字符串中的版本号
SEMVER_RE = re.compile(
    r'(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)'
    r'(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)'
    r'(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?\b'
)


def semver_gt(a: str, b: str) -> bool:
    """比较两个 semver 版本串，返回 a > b。

    规则（ADR-0001）：
    - 标准 semver：先比 major，再 minor，再 patch
    - prerelease 版本 < 稳定版本（无 prerelease 段）
    - 相同主版本下，prerelease 段按点分逐段比较：
      - 数字段按数值比较
      - 字母段按 ASCII 比较
    - 非法版本串返回 False（不大于任何合法版本）

    Examples:
        >>> semver_gt("0.1.0", "0.1.0-rc.7")
        True
        >>> semver_gt("0.1.1-rc.2", "0.1.0")
        True
        >>> semver_gt("1.2.3", "1.2.3")
        False
        >>> semver_gt("invalid", "1.0.0")
        False
    """
    def parse(v: str) -> Optional[tuple]:
        m = SEMVER_RE.match(v.strip())
        if not m:
            return None
        major = int(m.group("major"))
        minor = int(m.group("minor"))
        patch = int(m.group("patch"))
        prerelease = m.group("prerelease") or ""
        prerelease_parts = prerelease.split(".") if prerelease else []
        return (major, minor, patch, prerelease_parts, prerelease != "")

    pa = parse(a)
    pb = parse(b)
    if not pa or not pb:
        return False

    # 比较 major, minor, patch
    for i in range(3):
        if pa[i] > pb[i]:
            return True
        if pa[i] < pb[i]:
            return False

    # 主版本相同，比较 prerelease
    pa_has_pre = pa[4]
    pb_has_pre = pb[4]

    # 有 prerelease < 无 prerelease
    if pa_has_pre and not pb_has_pre:
        return False
    if not pa_has_pre and pb_has_pre:
        return True
    if not pa_has_pre and not pb_has_pre:
        return False  # 完全相同

    # 都有 prerelease，逐段比较
    min_len = min(len(pa[3]), len(pb[3]))
    for i in range(min_len):
        pa_part = pa[3][i]
        pb_part = pb[3][i]
        # 数字段 vs 字母段
        pa_is_num = pa_part.isdigit()
        pb_is_num = pb_part.isdigit()
        if pa_is_num and pb_is_num:
            pa_num = int(pa_part)
            pb_num = int(pb_part)
            if pa_num > pb_num:
                return True
            if pa_num < pb_num:
                return False
        elif pa_is_num and not pb_is_num:
            return False  # 数字 < 字母
        elif not pa_is_num and pb_is_num:
            return True  # 字母 > 数字
        else:
            # 都是字母，ASCII 比较
            if pa_part > pb_part:
                return True
            if pa_part < pb_part:
                return False

    # 所有公共段相同，更长者为大（1.0.0-alpha.1 > 1.0.0-alpha）
    return len(pa[3]) > len(pb[3])


def _resolve_registry() -> str:
    """解析 npm registry 地址（三级回退）。

    顺序（ADR-0001）：
    1. npm config get registry（子进程，5s 超时）
    2. 解析 ~/.npmrc 的 registry= 行
    3. 回退到官方 registry.npmjs.org
    """
    # 尝试 npm config get registry
    try:
        result = subprocess.run(
            ["npm", "config", "get", "registry"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            registry = result.stdout.strip()
            if registry:
                return registry
    except (subprocess.SubprocessError, OSError):
        pass

    # 回退解析 ~/.npmrc
    try:
        npmrc_path = Path.home() / ".npmrc"
        if npmrc_path.is_file():
            for line in npmrc_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                # 忽略注释（; 或 # 开头）和空行
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                if line.startswith("registry="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass

    # 最终回退
    return OFFICIAL_REGISTRY


def fetch_latest_version(registry: str) -> Optional[str]:
    """从 registry 获取 dsh 最新版本号。

    Args:
        registry: registry 地址（如 https://registry.npmjs.org）

    Returns:
        版本串（如 "1.2.3"），失败返回 None

    超时/非 200/坏 JSON → None（不抛异常）
    """
    # URL 编码 scoped 包名：@deepseek-ai/dsh -> @deepseek-ai%2Fdsh
    encoded_package = urllib.parse.quote(DSH_NPM_PACKAGE, safe="")
    url = f"{registry}/{encoded_package}/latest"

    try:
        # 连接+读取总超时约 10 秒
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.getcode() != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
            return data.get("version")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def current_version(dsh_argv: list[str]) -> Optional[str]:
    """执行 dsh --version 获取当前版本。

    Args:
        dsh_argv: dsh 命令 argv 数组，首元素为可执行文件路径

    Returns:
        版本串，失败返回 None

    使用绝对路径执行（不依赖 PATH），10s 超时。
    Windows 使用 CREATE_NO_WINDOW 避免弹窗。
    """
    if not dsh_argv:
        return None

    cmd = [dsh_argv[0], "--version"]
    try:
        kwargs: Dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
        # Windows：无窗口启动
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        result = subprocess.run(cmd, **kwargs)
        if result.returncode != 0:
            return None

        # 从输出中提取第一个 semver 匹配
        output = result.stdout
        match = SEMVER_RE.search(output)
        if match:
            return match.group(0)
        return None
    except (subprocess.SubprocessError, OSError):
        return None

def _fetch_git_remote(repo_path: str, remote_ref: str = "origin/main") -> Dict[str, Any]:
    """fetch git 远端并检查落后提交数。

    Args:
        repo_path: git 仓库路径
        remote_ref: 远端引用（默认 origin/main）

    Returns:
        判定结果字典：
        {
            "status": "behind" | "current" | "unknown",  # 状态
            "behind_count": int,                          # 落后提交数
            "reason": str,                                # 未知状态原因
        }

    超时/非零退出/异常 → status="unknown"。
    """
    result = {
        "status": "unknown",
        "behind_count": 0,
        "reason": "",
    }

    # 1. git fetch（超时 5 秒）
    try:
        fetch_cmd = ["git", "-C", repo_path, "fetch", "--quiet", remote_ref]
        subprocess.run(fetch_cmd, capture_output=True, timeout=5, check=True)
    except subprocess.TimeoutExpired:
        result["reason"] = "git fetch timeout"
        return result
    except subprocess.CalledProcessError as e:
        # 非 128 错误可能是网络问题，也视为未知
        stderr = ""
        if hasattr(e, "stderr") and e.stderr:
            # Handle both bytes and string
            if isinstance(e.stderr, bytes):
                stderr = e.stderr.decode("utf-8", errors="ignore").lower()
            else:
                stderr = str(e.stderr).lower()
        
        if "not a git repository" in stderr:
            result["reason"] = "not a git repository"
        elif "does not appear to be a git repository" in stderr:
            result["reason"] = "no remote configured"
        else:
            result["reason"] = f"git fetch failed: {e.returncode}"
        return result
    except (OSError, FileNotFoundError):
        result["reason"] = "git command not found"
        return result

    # 2. 检查是否 detached HEAD
    try:
        # git symbolic-ref HEAD 应该返回 refs/heads/main
        symbolic_cmd = ["git", "-C", repo_path, "symbolic-ref", "--short", "HEAD"]
        subprocess.run(symbolic_cmd, capture_output=True, timeout=5, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        result["reason"] = "detached HEAD"
        return result

    # 3. 统计落后提交数：HEAD..origin/main
    try:
        count_cmd = ["git", "-C", repo_path, "rev-list", "--count", f"HEAD..{remote_ref}"]
        count_result = subprocess.run(count_cmd, capture_output=True, timeout=5, check=True)
        behind_count = int(count_result.stdout.strip())
        
        result["behind_count"] = behind_count
        if behind_count > 0:
            result["status"] = "behind"
        else:
            result["status"] = "current"
        return result
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
        result["reason"] = f"failed to count commits: {str(e)}"
        return result


def check_for_update(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """检查是否有新版本可更新。

    Args:
        cfg: 配置字典（含 dshType, dshArgv 等）

    Returns:
        判定结果字典：
        {
            "current_version": Optional[str],  # 当前版本
            "latest_version": Optional[str],   # 最新版本
            "has_update": Optional[bool],      # 是否有更新（pnpm/manual 为 None）
            "reason": str,                     # 无更新或失败原因
        }

    pnpm/manual 类型本票返回「未知，跳过判定」（票 02 处理 pnpm）。
    """
    dsh_type = cfg.get("dshType", "")

    # pnpm 源码安装：git 判定（票 02）
    if dsh_type == "pnpm":
        dsh_dir = cfg.get("dshDir", "")
        if not dsh_dir:
            return {
                "current_version": None,
                "latest_version": None,
                "has_update": None,
                "behind_count": 0,
                "reason": "未知，跳过判定（无 dshDir）",
            }
        
        git_result = _fetch_git_remote(dsh_dir)
        if git_result["status"] == "unknown":
            return {
                "current_version": None,
                "latest_version": None,
                "has_update": None,
                "behind_count": 0,
                "reason": "未知，跳过判定",
            }
        elif git_result["status"] == "behind":
            behind = git_result["behind_count"]
            return {
                "current_version": None,
                "latest_version": None,
                "has_update": True,
                "behind_count": behind,
                "reason": f"有更新（落后 {behind} 提交）",
            }
        else:  # current
            return {
                "current_version": None,
                "latest_version": None,
                "has_update": False,
                "behind_count": 0,
                "reason": "已是最新",
            }

    # manual：不支持（票 02）
    if dsh_type == "manual":
        return {
            "current_version": None,
            "latest_version": None,
            "has_update": None,
            "behind_count": 0,
            "reason": "未知，跳过判定",
        }

    # global/local：走 registry 检测
    if dsh_type not in ("global", "local"):
        return {
            "current_version": None,
            "latest_version": None,
            "has_update": None,
            "reason": f"未知安装类型: {dsh_type}",
        }

    # 获取当前版本
    dsh_argv = cfg.get("dshArgv", [])
    current = current_version(dsh_argv)
    if current is None:
        return {
            "current_version": None,
            "latest_version": None,
            "has_update": False,
            "reason": "检查失败：无法获取当前版本",
        }

    # 获取最新版本
    registry = _resolve_registry()
    latest = fetch_latest_version(registry)
    if latest is None:
        return {
            "current_version": current,
            "latest_version": None,
            "has_update": False,
            "reason": "检查失败：无法获取最新版本",
        }

    # 判定是否有更新
    if semver_gt(latest, current):
        return {
            "current_version": current,
            "latest_version": latest,
            "has_update": True,
            "reason": "",
        }
    else:
        return {
            "current_version": current,
            "latest_version": latest,
            "has_update": False,
            "reason": "已是最新版本",
        }