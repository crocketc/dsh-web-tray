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
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsh_process import build_subprocess_env

# 包名常量
DSH_NPM_PACKAGE = "@deepseek-ai/dsh"
# 官方 registry（最终回退）
OFFICIAL_REGISTRY = "https://registry.npmjs.org"
# 平台检测
IS_WINDOWS = sys.platform == "win32"

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


def _ssl_context() -> ssl.SSLContext:
    """构造带系统 CA 的 SSL 上下文。

    PyInstaller 打包的 Python 找不到 macOS 系统证书存储（/etc/ssl/cert.pem），
    HTTPS 请求会在证书校验处直接失败（打包版「无法获取最新版本」的根因）；
    显式把系统证书文件补进信任列表即可修复，且对源码/其他平台无害。
    """
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    except (OSError, ssl.SSLError):
        pass  # 非 macOS / 文件缺失时保持默认行为
    return ctx


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
        # 连接+读取总超时约 10 秒；显式带系统 CA（打包版 ssl 找不到证书）
        with urllib.request.urlopen(url, timeout=10, context=_ssl_context()) as response:
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

    子进程环境用 build_subprocess_env 增强 PATH：dsh 多为 node 脚本
    （shebang #!/usr/bin/env node），Finder/LaunchAgent 启动的 GUI 应用
    PATH 只有系统目录，解释器找不到会 127 退出（macOS GUI PATH 陷阱）。
    """
    if not dsh_argv:
        return None

    cmd = [dsh_argv[0], "--version"]
    try:
        kwargs: Dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 10,
            # macOS GUI 应用 PATH 受限：补常见工具目录（dsh_process 同款对策）
            "env": build_subprocess_env(),
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
        # remote_ref 格式为 "origin/main"，需要拆分为 remote 和 branch
        parts = remote_ref.split("/", 1)
        if len(parts) == 2:
            remote, branch = parts
            fetch_cmd = ["git", "-C", repo_path, "fetch", "--quiet", remote, branch]
        else:
            # 如果没有斜杠，直接使用
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


def local_installed_version(dsh_dir: str) -> Optional[str]:
    """读取本地安装（node_modules）的已装 dsh 版本号。

    Args:
        dsh_dir: 含 node_modules 的基准目录（配置 dshDir，local 安装时向导保存）

    Returns:
        版本串，失败返回 None

    local 类型的 dshArgv[0] 是 npx（`npx --version` 给出的是 npm 版本），
    因此本地安装一律读 node_modules/@deepseek-ai/dsh/package.json。
    """
    if not dsh_dir:
        return None
    pkg_json = Path(dsh_dir) / "node_modules" / DSH_NPM_PACKAGE / "package.json"
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        version = data.get("version")
        return version if isinstance(version, str) and version else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


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
    if dsh_type == "local":
        # local：dshArgv[0] 是 npx，--version 给的是 npm 版本 → 读包描述文件
        current = local_installed_version(cfg.get("dshDir", ""))
    else:
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


def build_upgrade_command(cfg: Dict[str, Any], target_version: str) -> Dict[str, Any]:
    """按安装类型构造升级命令与手动命令文案。

    Args:
        cfg: 配置字典（含 dshType, dshDir 等）
        target_version: 目标版本号（如 "1.2.3" 或 "1.2.3-rc.1"）

    Returns:
        {
            "argv": Optional[List[str]],  # 命令 argv 数组（manual 为 None）
            "cwd": Optional[str],         # 工作目录（global 无需）
            "manual_text": str,           # 手动命令文案（失败通知用）
        }

    四种类型：
    - global: npm install -g @deepseek-ai/dsh@<version>
    - local: cwd=dshDir 下 npm install @deepseek-ai/dsh@<version>
    - pnpm: cwd=dshDir 下 git pull && pnpm install && pnpm run build
    - manual: 无命令，仅打开发布页 URL
    """
    dsh_type = cfg.get("dshType", "")
    dsh_dir = cfg.get("dshDir", "")
    package_at_version = f"{DSH_NPM_PACKAGE}@{target_version}"
    result: Dict[str, Any] = {
        "argv": None,
        "cwd": None,
        "manual_text": "",
    }

    if dsh_type == "global":
        # 全局安装：npm install -g 包@版本
        result["argv"] = ["npm", "install", "-g", package_at_version]
        result["manual_text"] = f"npm install -g {package_at_version}"

    elif dsh_type == "local":
        # 本地安装：在 dshDir 下执行
        result["argv"] = ["npm", "install", package_at_version]
        if dsh_dir:
            result["cwd"] = dsh_dir
            result["manual_text"] = f"cd {dsh_dir} && npm install {package_at_version}"
        else:
            # dshDir 为空时在当前目录执行
            result["manual_text"] = f"npm install {package_at_version}"

    elif dsh_type == "pnpm":
        # 源码安装：git pull + pnpm install + pnpm run build
        if sys.platform == "win32":
            # Windows 用 cmd /c 和 & 串联
            cmd = "git pull & pnpm install & pnpm run build"
            result["argv"] = ["cmd", "/c", cmd]
        else:
            # POSIX 用 sh -c 和 && 串联（前序失败则停止）
            cmd = "git pull && pnpm install && pnpm run build"
            result["argv"] = ["/bin/sh", "-c", cmd]
        if dsh_dir:
            result["cwd"] = dsh_dir
            result["manual_text"] = f"cd {dsh_dir} && {cmd}"
        else:
            result["manual_text"] = cmd

    elif dsh_type == "manual":
        # 手动安装：无命令，仅打开发布页
        result["manual_text"] = (
            "请手动升级：访问 https://github.com/deepseek-ai/dsh/releases "
            f"下载 {target_version} 版本"
        )
        # argv 保持 None（不执行命令）

    else:
        # 未知类型
        result["manual_text"] = f"未知安装类型: {dsh_type}"

    return result

# 升级日志轮转大小（5MB，与 dsh_process 保持一致）
UPGRADE_LOG_ROTATE_BYTES = 5 * 1024 * 1024
# 升级执行超时（30分钟）
UPGRADE_TIMEOUT_SECONDS = 30 * 60


def _rotate_upgrade_log_if_needed(log_path: Path) -> None:
    """日志文件超过限制时轮转（.1 后缀）。"""
    try:
        if log_path.exists() and log_path.stat().st_size > UPGRADE_LOG_ROTATE_BYTES:
            # 重命名为 .1
            rotated = log_path.with_suffix(log_path.suffix + ".1")
            log_path.replace(rotated)
    except OSError:
        pass  # 轮转失败不影响主流程


def _get_stderr_tail(log_path: Path, max_chars: int = 500) -> str:
    """从日志文件中提取 stderr 尾部摘要。

    读取最后约 max_chars 字符，尽量按行截断。
    """
    try:
        if not log_path.exists():
            return ""
        content = log_path.read_text(encoding="utf-8", errors="replace")
        if len(content) <= max_chars:
            return content
        # 从最后往前找换行符，尽量按行截断
        tail = content[-max_chars:]
        first_newline = tail.find("\n")
        if first_newline > 0:
            return tail[first_newline + 1:]
        return tail
    except OSError:
        return ""


def execute_upgrade(
    argv: List[str],
    cwd: Optional[str],
    log_path: str,
    on_done: callable,
) -> None:
    """在后台线程静默执行升级命令。

    Args:
        argv: 命令 argv 数组（如 ["npm", "install", "-g", "@deepseek-ai/dsh@1.2.3"]）
        cwd: 工作目录（global 安装为 None）
        log_path: 升级日志文件路径
        on_done: 回调函数，签名 on_done(exit_code: int, stderr_tail: str)

    执行特性：
    - Windows: CREATE_NO_WINDOW（无窗口）
    - POSIX: start_new_session（无终端）
    - stdout/stderr 合并写日志文件（含轮转）
    - 超时后终止子进程
    - 失败时返回 stderr 尾部摘要（约 500 字符）
    """
    def _run() -> None:
        log_file_path = Path(log_path)
        proc = None
        exit_code = -1
        stderr_tail = ""

        try:
            # 轮转旧日志
            _rotate_upgrade_log_if_needed(log_file_path)

            # 准备子进程参数
            kwargs: Dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,  # 合并到 stdout
                "stdin": subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if cwd:
                kwargs["cwd"] = cwd

            # 平台特定参数
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True

            # 启动子进程
            proc = subprocess.Popen(argv, **kwargs)

            # 写日志（参考 dsh_process._drain）
            log_file = None
            try:
                log_file_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = log_file_path.open("a", encoding="utf-8")
            except OSError:
                log_file = None

            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if log_file is not None:
                        try:
                            log_file.write(line)
                            log_file.flush()
                        except OSError:
                            log_file = None
            finally:
                if log_file is not None:
                    try:
                        log_file.close()
                    except OSError:
                        pass
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except OSError:
                        pass

            # 等待进程结束（带超时）
            try:
                exit_code = proc.wait(timeout=UPGRADE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                # 超时：终止进程
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                exit_code = -1  # 超时标记
                stderr_tail = "升级超时（超过30分钟），已终止进程"

            # 提取 stderr 尾部（仅当非超时且非零退出时）
            if exit_code != 0 and not stderr_tail:
                stderr_tail = _get_stderr_tail(log_file_path)

        except Exception as e:
            # 异常情况
            exit_code = -1
            stderr_tail = f"升级执行异常: {str(e)}"
        finally:
            # 回调通知
            try:
                on_done(exit_code, stderr_tail)
            except Exception:
                pass  # 回调失败不影响主流程

    # 在后台线程执行
    thread = threading.Thread(target=_run, name="upgrade-executor", daemon=True)
    thread.start()
