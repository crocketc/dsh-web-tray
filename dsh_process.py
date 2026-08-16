"""dsh web 子进程生命周期管理（跨平台核心）。

依据 dsh 源码核实的 supervisor 契约：
- stdout 的 ``dsh web: http://...`` 行是官方就绪信号（packages/bundle/web-app/src/index.ts，
  打印时所有 API 路由已挂载完毕）——不轮询端口。
- SIGTERM 是官方约定的 supervisor 优雅停止信号，exit 0，dsh 自行 ``fiber.dispose()``
  清理整棵进程树（apps/cli/src/profile-boot.ts）。
- ``--port 0`` 支持系统自动分配端口，实际地址从 URL 行回读。

四个阶段：启动（PATH 解析 + spawn）→ 就绪（解析 URL 行）→ 运行监控（崩溃感知）→
退出（SIGTERM 优先，树杀兜底）。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

READY_RE = re.compile(r"dsh web: (http://\S+)")

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

try:  # psutil 是依赖项，但缺失时核心启动/就绪/监控仍可用（仅 POSIX 树杀兜底降级）
    import psutil
except ImportError:  # pragma: no cover - 环境异常兜底
    psutil = None

#: 日志文件超过该大小后轮转（字节）
LOG_ROTATE_BYTES = 5 * 1024 * 1024

#: 模块级持有 Job Object 句柄：句柄关闭（含本进程被杀）时整棵子树陪葬
_job_handles: list = []


def _assign_to_kill_on_close_job(proc: "subprocess.Popen") -> None:
    """Windows：把子进程纳入 Job Object（KILL_ON_JOB_CLOSE）。

    托盘进程本身被硬杀（断电、任务管理器、宿主崩溃）时，内核自动终止整棵
    dsh 进程树，不留孤儿。句柄必须保持存活（模块级列表持有），失败静默降级。
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        import ctypes.wintypes as wt

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),  # ULONG_PTR
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD),
            ]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9
        PROCESS_TERMINATE = 0x0001
        PROCESS_SET_QUOTA = 0x0200

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # 句柄是 64 位指针量：不声明 restype 会被截断成 32 位 int
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        info = EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        hjob = k32.CreateJobObjectW(None, None)
        if not hjob:
            return
        if not k32.SetInformationJobObject(
            hjob, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            k32.CloseHandle(hjob)
            return
        hproc = k32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, proc.pid)
        if not hproc:
            # 受限环境（如沙箱拒绝 PROCESS_SET_QUOTA）会走到这里：静默降级
            k32.CloseHandle(hjob)
            return
        try:
            if k32.AssignProcessToJobObject(hjob, hproc):
                _job_handles.append(hjob)  # 必须持有，进程退出时自动触发树终止
            else:
                k32.CloseHandle(hjob)
        finally:
            k32.CloseHandle(hproc)
    except Exception:
        pass  # Job Object 失败仅意味着失去"托盘被杀→树陪葬"保护，其余功能不受影响


def resolve_command(argv: Sequence[str]) -> Optional[List[str]]:
    """把 argv[0] 解析为绝对路径的完整 argv。

    返回 None 表示找不到命令（触发安装引导流程）。

    - Windows：``pnpm`` 实为 ``pnpm.cmd`` shim，CreateProcess 不解析 .cmd，
      必须 ``shutil.which()`` 拿全路径（dsh 源码 plugin.ts 有同样的坑与兜底）。
    - macOS：从 Finder / LaunchAgent 启动的 GUI 应用 PATH 只有系统路径，
      用登录 shell 拿真实 PATH 兜底。
    """
    if not argv:
        return None
    exe = shutil.which(argv[0])
    if exe:
        return [exe, *argv[1:]]
    if IS_WINDOWS:
        # 防御：手输的 npm shim 路径缺扩展名（如 ...\npm\dsh 而非 dsh.cmd）。
        # 无扩展名文件是 POSIX sh 包装，Popen 直接执行必失败（WinError 193）。
        p = Path(argv[0])
        if p.exists() and not p.suffix:
            for ext in (".cmd", ".bat", ".exe"):
                cand = p.with_suffix(ext)
                if cand.exists():
                    return [str(cand), *argv[1:]]
    if not IS_WINDOWS:
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            result = subprocess.run(
                [shell, "-l", "-c", f"command -v {shlex.quote(argv[0])}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return [result.stdout.strip().strip("\n"), *argv[1:]]
        except (subprocess.SubprocessError, OSError):
            pass
    return None


def parse_command_line(cmdline: str) -> List[str]:
    """把用户手输的命令字符串拆成 argv（尊重引号，Windows 路径反斜杠不转义）。"""
    cmdline = cmdline.strip()
    if not cmdline:
        return []
    try:
        tokens = shlex.split(cmdline, posix=not IS_WINDOWS)
    except ValueError:
        tokens = cmdline.split()
    # posix=False 保留引号；posix=True 不影响无引号 token。统一剥掉包裹引号。
    out = []
    for tok in tokens:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
            tok = tok[1:-1]
        out.append(tok)
    return out


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """检测端口是否已有监听者（dsh 仅绑定 loopback，直接试探连接即可）。"""
    if not port or port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _rotate_log_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > LOG_ROTATE_BYTES:
            log_path.replace(log_path.with_suffix(log_path.suffix + ".old"))
    except OSError:
        pass


class DshProcess:
    """dsh web 子进程的完整生命周期管理。"""

    def __init__(self, argv: Sequence[str], cwd: str, log_path: str,
                 extra_env: Optional[dict] = None):
        self.argv: List[str] = list(argv)
        self.cwd = cwd
        self.log_path = str(log_path)
        self.extra_env = dict(extra_env) if extra_env else None
        self.proc: Optional[subprocess.Popen] = None
        self.url: Optional[str] = None          # 就绪后从 URL 行解析
        self.exit_code: Optional[int] = None    # 进程退出后记录
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 启动
    def start(self) -> subprocess.Popen:
        """启动 dsh web（隐藏终端：Windows CREATE_NO_WINDOW / POSIX 独立进程组）。"""
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        with self._lock:
            self.url = None
            self.exit_code = None
            self._ready_event.clear()
        kwargs: dict = dict(
            cwd=self.cwd,
            stdout=subprocess.PIPE,   # 必须读，否则管道写满会阻塞 dsh
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if IS_WINDOWS:
            # 无终端窗口（进程仍有不可见的控制台）。常量在 macOS/Linux 上不存在，
            # 必须包在守卫里。
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # 独立进程组：脱离控制终端，托盘退出不受终端关闭影响
            kwargs["start_new_session"] = True
        if self.extra_env:
            env = dict(os.environ)
            env.update(self.extra_env)
            kwargs["env"] = env
        self.proc = subprocess.Popen(self.argv, **kwargs)
        if IS_WINDOWS:
            # 托盘自身被硬杀时，内核自动终止整棵 dsh 树（无孤儿）
            _assign_to_kill_on_close_job(self.proc)
        threading.Thread(target=self._drain, name="dsh-stdout-drain", daemon=True).start()
        return self.proc

    def _drain(self) -> None:
        """持续排空 stdout：既解析就绪行，也写日志。

        坑：PIPE 后不读，子进程 console.log 写满缓冲区后会永久阻塞。
        因此日志文件打开/写入失败时**绝不能中断排空**——继续读管道，只是不落盘。
        """
        log = None
        log_file = Path(self.log_path)
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log_if_needed(log_file)
            log = open(log_file, "a", encoding="utf-8")
        except OSError:
            log = None
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for line in self.proc.stdout:
                if log is not None:
                    try:
                        log.write(line)
                        log.flush()
                    except OSError:
                        log = None  # 落盘失败降级为纯解析
                if self.url is None:
                    m = READY_RE.search(line)
                    if m:
                        with self._lock:
                            self.url = m.group(1)
                        self._ready_event.set()
        finally:
            if log is not None:
                try:
                    log.close()
                except OSError:
                    pass
            if self.proc is not None and self.proc.stdout is not None:
                try:
                    self.proc.stdout.close()
                except OSError:
                    pass
            # EOF：等待退出码落地（watch 线程也在 wait，重复 wait 是安全的）
            if self.proc is not None:
                try:
                    self.exit_code = self.proc.wait(timeout=30)
                except Exception:
                    pass

    # ------------------------------------------------------------------ 就绪
    def wait_ready(self, timeout: float = 120) -> str:
        """阻塞等待 URL 行出现。

        首次启动含 profile bootstrap，可能远超 5 秒，故默认 120 秒；
        端口轮询既慢又不可靠（端口通 ≠ API 就绪）。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready_event.is_set() and self.url:
                return self.url
            if self.proc is None:
                raise RuntimeError("dsh web 尚未启动")
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"dsh web 启动即退出（code {self.proc.returncode}），查看日志 {self.log_path}"
                )
            time.sleep(0.2)
        raise TimeoutError(f"dsh web {timeout}s 内未就绪，查看日志 {self.log_path}")

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ------------------------------------------------------------------ 监控
    def watch(self, on_exit: Callable[[int, bool], None]) -> None:
        """后台线程：阻塞等待子进程退出，回调 (exit_code, had_been_ready)。"""
        ready_snapshot = lambda: bool(self.url)  # noqa: E731

        def loop() -> None:
            assert self.proc is not None
            code = self.proc.wait()
            had_ready = ready_snapshot()
            with self._lock:
                self.exit_code = code
            on_exit(code, had_ready)

        if self._watch_thread is None or not self._watch_thread.is_alive():
            self._watch_thread = threading.Thread(target=loop, name="dsh-watch", daemon=True)
            self._watch_thread.start()

    # ------------------------------------------------------------------ 退出
    def stop(self, timeout: float = 10) -> None:
        """优雅停止。

        - POSIX：SIGTERM（官方契约，exit 0，dsh 自行清理整棵树）；超时才升级树杀。
        - Windows：无法向无控制台进程投递 SIGTERM（CTRL 事件要求共享控制台），
          terminate() 只杀直接子进程（pnpm.cmd → cmd.exe），会留下 node 孤儿，
          故主路径直接 ``taskkill /T /F`` 树杀。
        """
        if self.proc is None or self.proc.poll() is not None:
            return
        if IS_WINDOWS:
            # Windows 无法向无控制台进程投递 SIGTERM（CTRL 事件要求共享控制台），
            # terminate() 只杀直接子进程（pnpm.cmd → cmd.exe → node 链会留孤儿），
            # 必须树杀。主路径用 psutil 进程内枚举（无需外部命令，受限环境也可靠）；
            # taskkill /T /F 仅作 psutil 缺失/枚举失败时的兜底。
            self._tree_kill_windows()
            try:
                self.proc.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return
        # POSIX：SIGTERM 优先（官方 supervisor 契约）
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        # 兜底：杀整棵进程树（dsh web 运行期会派生 agent/后台任务子进程）
        self._tree_kill_posix()

    def _tree_kill_windows(self) -> None:
        assert self.proc is not None
        if psutil is not None:
            try:
                parent = psutil.Process(self.proc.pid)
                children = parent.children(recursive=True)
                # 先子后父，父进程来不及派生新的后代
                for child in children:
                    try:
                        child.kill()  # Windows 上 terminate() 即 TerminateProcess
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                return
            except psutil.NoSuchProcess:
                return
            except psutil.Error:
                pass  # 枚举失败 → 落到 taskkill 兜底
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15,
            )
        except (subprocess.SubprocessError, OSError):
                pass

    def _tree_kill_posix(self) -> None:
        assert self.proc is not None
        if psutil is None:  # pragma: no cover
            self.proc.kill()
            return
        try:
            parent = psutil.Process(self.proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(children, timeout=5)
            parent.kill()
        except psutil.NoSuchProcess:
            pass
