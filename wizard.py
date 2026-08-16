"""DSH Web Tray 配置向导（tkinter）。

**必须以子进程运行**（macOS 上 tkinter 与 pystray 都要求主线程，从托盘回调弹
tkinter 窗口会挂起/崩溃）：主程序 ``subprocess.Popen([sys.executable, wizard.py])``，
结果经配置文件回传，退出码 0=已保存 / 1=取消。

也可独立运行：``python wizard.py [--install-only]``
"""
from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

import config as cfgmod
import detect
from dsh_process import DshProcess, parse_command_line, resolve_command

WINDOW_TITLE = "DSH Web Tray 配置向导"

TYPE_LABELS = {
    "pnpm": "源码安装（pnpm dsh web）",
    "global": "全局安装（dsh web）",
    "local": "本地安装（npx）",
    "manual": "手动配置",
}


class WizardApp:
    def __init__(self, root: tk.Tk, install_only: bool = False) -> None:
        self.root = root
        self.install_only = install_only
        self.candidates: List[Dict[str, Any]] = []
        self.selected: Optional[Dict[str, Any]] = None
        self._test_worker: Optional[threading.Thread] = None
        self._saved = False

        root.title(WINDOW_TITLE)
        root.minsize(620, 480)
        try:
            root.geometry("680x560")
        except tk.TclError:
            pass

        self.container = ttk.Frame(root, padding=16)
        self.container.pack(fill=tk.BOTH, expand=True)

        self._build_detect_screen()
        self.refresh_detection()

    # ------------------------------------------------------------- 检测屏
    def _build_detect_screen(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()

        ttk.Label(
            self.container,
            text="DSH 安装检测",
            font=("", 14, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            self.container,
            text="自动检测本机的 dsh 安装方式；检测通过后命令会解析为绝对路径保存，"
            "运行期不再依赖 PATH。",
            wraplength=620,
            foreground="#666",
        ).pack(anchor=tk.W, pady=(0, 10))

        # 候选列表区（滚动）
        list_wrap = ttk.Frame(self.container)
        list_wrap.pack(fill=tk.BOTH, expand=True)
        self.cand_canvas = tk.Canvas(list_wrap, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.cand_canvas.yview)
        self.cand_inner = ttk.Frame(self.cand_canvas)
        self.cand_inner.bind(
            "<Configure>", lambda e: self.cand_canvas.configure(scrollregion=self.cand_canvas.bbox("all"))
        )
        self.cand_canvas.create_window((0, 0), window=self.cand_inner, anchor=tk.NW)
        self.cand_canvas.configure(yscrollcommand=scrollbar.set)
        self.cand_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 端口
        port_row = ttk.Frame(self.container)
        port_row.pack(fill=tk.X, pady=(10, 6))
        ttk.Label(port_row, text="端口号（0 = 系统自动分配，实际地址从启动日志回读）：").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self._initial_port()))
        port_entry = ttk.Entry(port_row, textvariable=self.port_var, width=8)
        port_entry.pack(side=tk.LEFT, padx=6)

        # 检测状态/安装引导区
        self.hint_var = tk.StringVar(value="")
        self.hint_label = ttk.Label(self.container, textvariable=self.hint_var, wraplength=620, foreground="#a00")
        self.hint_label.pack(anchor=tk.W, pady=(0, 4))

        self.guide_frame = ttk.Frame(self.container)
        # guide_frame 仅在无候选时 pack

        # 按钮区
        btns = ttk.Frame(self.container)
        btns.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btns, text="重试检测", command=self.refresh_detection).pack(side=tk.LEFT)
        ttk.Button(btns, text="手动配置…", command=self._build_manual_screen).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", command=self.root.destroy).pack(side=tk.RIGHT)
        self.save_btn = ttk.Button(btns, text="保存并启动", command=self._save_from_detect)
        self.save_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _initial_port(self) -> int:
        existing = cfgmod.load_config()
        if existing:
            return int(existing.get("dshPort") or cfgmod.DEFAULT_PORT)
        return cfgmod.DEFAULT_PORT

    def refresh_detection(self) -> None:
        self.candidates = detect.detect_all()
        for child in self.cand_inner.winfo_children():
            child.destroy()
        self.hint_var.set("")
        self.guide_frame.pack_forget()

        if not self.candidates:
            self.hint_var.set("未检测到任何 dsh 安装。请按下方指引安装，或选择手动配置。")
            self._build_install_guide()
            self.save_btn.state(["disabled"])
            return

        self.selected = self.candidates[0]
        self.cand_var = tk.StringVar(value="0")
        for idx, cand in enumerate(self.candidates):
            self._add_candidate_row(idx, cand)

        first = self.candidates[0]
        if first["type"] == "pnpm" and not first.get("dist_built"):
            self._show_build_hint(first)

    def _add_candidate_row(self, idx: int, cand: Dict[str, Any]) -> None:
        row = ttk.Frame(self.cand_inner, padding=(0, 6))
        row.pack(fill=tk.X, anchor=tk.W)
        rb = ttk.Radiobutton(
            row,
            variable=self.cand_var,
            value=str(idx),
            text=TYPE_LABELS.get(cand["type"], cand["type"]),
            command=lambda: self._on_select(idx),
        )
        rb.pack(anchor=tk.W)
        detail = cand.get("display", "")
        if cand.get("dir"):
            detail = f"{detail}    （{cand['dir']}）"
        ttk.Label(row, text=detail, foreground="#666", padding=(22, 0)).pack(anchor=tk.W)
        if cand["type"] == "pnpm":
            built = cand.get("dist_built")
            mark = "✅ 前端已构建" if built else "⚠️ 前端 dist 未构建（启动会失败）"
            ttk.Label(row, text=mark, foreground=("#0a0" if built else "#a00"), padding=(22, 0)).pack(anchor=tk.W)

    def _on_select(self, idx: int) -> None:
        self.selected = self.candidates[idx]
        if self.selected["type"] == "pnpm" and not self.selected.get("dist_built"):
            self._show_build_hint(self.selected)
        else:
            self.hint_var.set("")

    def _show_build_hint(self, cand: Dict[str, Any]) -> None:
        self.hint_var.set(
            "源码安装的前端尚未构建，dsh web 启动即会失败。"
            "请先在仓库根目录执行构建命令。"
        )
        for w in self.guide_frame.winfo_children():
            w.destroy()
        row = ttk.Frame(self.guide_frame)
        row.pack(anchor=tk.W)
        ttk.Button(
            row,
            text="复制构建命令",
            command=lambda: self._copy(cand.get("build_hint") or "pnpm install && pnpm run build"),
        ).pack(side=tk.LEFT)
        self.guide_frame.pack(anchor=tk.W, pady=(4, 0))

    def _build_install_guide(self) -> None:
        """未安装时的完整安装指引（三种方式 + 复制 + 文档 + 手动配置）。"""
        for w in self.guide_frame.winfo_children():
            w.destroy()
        box = ttk.LabelFrame(self.guide_frame, text="安装指引", padding=10)
        box.pack(fill=tk.BOTH, expand=True)

        ttk.Label(box, text="① 全局安装（推荐）— 所有用户可用", font=("", 10, "bold")).pack(anchor=tk.W, pady=(4, 0))
        r1 = ttk.Frame(box)
        r1.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(r1, text=detect.INSTALL_COMMANDS["global"], foreground="#0a58ca").pack(side=tk.LEFT)
        ttk.Button(r1, text="复制", width=5, command=lambda: self._copy(detect.INSTALL_COMMANDS["global"])).pack(side=tk.LEFT, padx=6)

        ttk.Label(box, text="② 源码安装（开发者）— 可修改源码，需构建前端", font=("", 10, "bold")).pack(anchor=tk.W, pady=(8, 0))
        r2 = ttk.Frame(box)
        r2.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(r2, text=detect.INSTALL_COMMANDS["source"], foreground="#0a58ca", wraplength=560, justify=tk.LEFT).pack(side=tk.LEFT)
        ttk.Button(r2, text="复制", width=5, command=lambda: self._copy(detect.INSTALL_COMMANDS["source"])).pack(side=tk.LEFT, padx=6)

        ttk.Label(box, text="③ 手动配置 — 已安装但未检测到", font=("", 10, "bold")).pack(anchor=tk.W, pady=(8, 0))

        nav = ttk.Frame(box)
        nav.pack(anchor=tk.W, pady=(10, 0))
        ttk.Button(nav, text="打开手动配置", command=self._build_manual_screen).pack(side=tk.LEFT)
        ttk.Button(nav, text="访问官方文档", command=lambda: detect.platforms_open_docs()).pack(side=tk.LEFT, padx=8)

        self.guide_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    # ------------------------------------------------------------- 手动配置屏
    def _build_manual_screen(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()

        ttk.Label(self.container, text="手动配置 DSH Web GUI", font=("", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(
            self.container,
            text="命令会解析为绝对路径保存（存 argv 数组，路径含空格也安全）。",
            foreground="#666",
        ).pack(anchor=tk.W, pady=(0, 10))

        form = ttk.Frame(self.container)
        form.pack(fill=tk.BOTH, expand=True)

        ttk.Label(form, text="安装类型：").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.mtype_var = tk.StringVar(value="global")
        type_row = ttk.Frame(form)
        type_row.grid(row=0, column=1, sticky=tk.W)
        for val, label in (("pnpm", "源码 (pnpm)"), ("global", "全局 (dsh)"), ("local", "本地 (npx)")):
            ttk.Radiobutton(type_row, value=val, text=label, variable=self.mtype_var).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(form, text="DSH 工作目录：").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.dir_var = tk.StringVar(value="")
        dir_row = ttk.Frame(form)
        dir_row.grid(row=1, column=1, sticky=tk.EW)
        ttk.Entry(dir_row, textvariable=self.dir_var, width=52).pack(side=tk.LEFT)
        ttk.Button(dir_row, text="浏览…", command=self._browse_dir).pack(side=tk.LEFT, padx=6)

        ttk.Label(form, text="启动命令：").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.cmd_var = tk.StringVar(value="dsh web")
        ttk.Entry(form, textvariable=self.cmd_var, width=56).grid(row=2, column=1, sticky=tk.EW)
        ttk.Label(form, text="（源码安装示例：pnpm dsh web；全局：dsh web）", foreground="#999").grid(
            row=3, column=1, sticky=tk.W
        )

        ttk.Label(form, text="端口号：").grid(row=4, column=0, sticky=tk.W, pady=4)
        self.mport_var = tk.StringVar(value=str(self._initial_port()))
        port_row = ttk.Frame(form)
        port_row.grid(row=4, column=1, sticky=tk.W)
        ttk.Entry(port_row, textvariable=self.mport_var, width=8).pack(side=tk.LEFT)
        ttk.Label(port_row, text="（0 = 自动分配）", foreground="#999").pack(side=tk.LEFT, padx=6)

        form.columnconfigure(1, weight=1)

        self.test_var = tk.StringVar(value="")
        ttk.Label(self.container, textvariable=self.test_var, wraplength=620, foreground="#0a0").pack(anchor=tk.W, pady=(8, 0))
        self.test_progress = ttk.Progressbar(self.container, mode="indeterminate", length=240)

        btns = ttk.Frame(self.container)
        btns.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(btns, text="← 返回检测", command=self._build_detect_screen_and_refresh).pack(side=tk.LEFT)
        self.test_btn = ttk.Button(btns, text="测试连接", command=self._test_connection)
        self.test_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", command=self.root.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="保存", command=self._save_from_manual).pack(side=tk.RIGHT, padx=(0, 8))

    def _build_detect_screen_and_refresh(self) -> None:
        self._build_detect_screen()
        self.refresh_detection()

    def _browse_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if chosen:
            self.dir_var.set(chosen)

    # ------------------------------------------------------------- 测试连接
    def _test_connection(self) -> None:
        """复用 DshProcess（spawn → 解析 URL 行 → 停止）。不轮询端口。"""
        if self._test_worker and self._test_worker.is_alive():
            return
        parsed = self._collect_manual()
        if parsed is None:
            return
        argv, cwd = parsed
        # 测试一律 --port 0：与正在运行的实例（如 3080）不冲突
        if "--port" not in argv:
            argv = [*argv, "--port", "0"]
        self.test_btn.state(["disabled"])
        self.test_var.set("正在启动 dsh web 测试（--port 0，不与现有实例冲突）…首次启动可能较慢")
        self.test_progress.pack(anchor=tk.W, pady=(4, 0))
        self.test_progress.start(24)

        def done(ok: bool, msg: str) -> None:
            self.test_progress.stop()
            self.test_progress.pack_forget()
            self.test_var.set(msg)
            self.test_btn.state(["!disabled"])

        def worker() -> None:
            log = str(cfgmod.log_dir() / "test-dsh-web.log")
            dsh = DshProcess(argv, cwd, log)
            try:
                dsh.start()
                url = dsh.wait_ready(timeout=120)  # 官方就绪信号
                self.root.after(0, lambda: done(True, f"✅ 配置成功！{url}"))
            except (RuntimeError, TimeoutError, OSError) as e:
                self.root.after(0, lambda: done(False, f"❌ 启动失败：{e}（日志：{log}）"))
            finally:
                dsh.stop()

        self._test_worker = threading.Thread(target=worker, daemon=True)
        self._test_worker.start()

    # ------------------------------------------------------------- 保存
    def _collect_manual(self) -> Optional[tuple]:
        port = self._validate_port(self.mport_var.get())
        if port is None:
            return None
        argv = parse_command_line(self.cmd_var.get())
        if not argv:
            messagebox.showerror(WINDOW_TITLE, "启动命令不能为空")
            return None
        resolved = resolve_command(argv)
        if not resolved:
            messagebox.showerror(
                WINDOW_TITLE,
                f"找不到命令：{argv[0]}\n请确认已安装并在 PATH 中，或改用绝对路径。",
            )
            return None
        d = self.dir_var.get().strip()
        cwd = d if d else str(Path.home())
        if not Path(cwd).is_dir():
            messagebox.showerror(WINDOW_TITLE, f"工作目录不存在：{cwd}")
            return None
        return resolved, cwd

    def _validate_port(self, raw: str) -> Optional[int]:
        try:
            port = int(raw.strip())
        except ValueError:
            messagebox.showerror(WINDOW_TITLE, "端口必须是 0-65535 的整数（0 = 自动分配）")
            return None
        if not (0 <= port <= 65535):
            messagebox.showerror(WINDOW_TITLE, "端口必须在 0-65535 之间（0 = 自动分配）")
            return None
        return port

    def _save_from_detect(self) -> None:
        if not self.selected:
            return
        port = self._validate_port(self.port_var.get())
        if port is None:
            return
        cand = self.selected
        resolved = resolve_command(cand["command"])
        if not resolved:
            messagebox.showerror(
                WINDOW_TITLE,
                f"找不到命令：{cand['command'][0]}\n请先安装（{cand['display']} 依赖的工具不在 PATH 上）。",
            )
            return
        if cand["type"] == "pnpm" and not cand.get("dist_built"):
            if not messagebox.askyesno(
                WINDOW_TITLE,
                "前端 dist 尚未构建，dsh web 启动会失败。\n"
                f"请先在 {cand['dir']} 执行：{cand.get('build_hint')}\n\n仍要保存此配置吗？",
            ):
                return
        self._persist(cand["type"], resolved, cand["display"], cand.get("dir") or "", port)

    def _save_from_manual(self) -> None:
        parsed = self._collect_manual()
        if parsed is None:
            return
        port = self._validate_port(self.mport_var.get())
        if port is None:
            return
        resolved, cwd = parsed
        display = " ".join(self.cmd_var.get().split())
        self._persist(self.mtype_var.get(), resolved, display, cwd, port)

    def _persist(self, dtype: str, argv: List[str], display: str, ddir: str, port: int) -> None:
        cfg = cfgmod.load_config() or cfgmod.default_config()
        cfg.update(
            {
                "dshType": dtype,
                "dshArgv": list(argv),
                "dshArgvDisplay": display,
                "dshDir": ddir,
                "dshPort": port,
            }
        )
        cfgmod.save_config(cfg)
        self._saved = True  # 退出码 0 的依据（主进程据此决定是否重载配置）
        messagebox.showinfo(WINDOW_TITLE, "配置已保存。")
        self.root.destroy()

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # 关窗后剪贴板仍保留


def _open_docs() -> None:
    import platforms

    platforms.open_url(detect.DOCS_URL)


# detect.py 不依赖 GUI，注入一个文档打开入口供指引使用
detect.platforms_open_docs = _open_docs  # type: ignore[attr-defined]


def run_wizard(install_only: bool = False) -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = WizardApp(root, install_only=install_only)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0 if getattr(app, "_saved", False) else 1


if __name__ == "__main__":
    sys.exit(run_wizard(install_only="--install-only" in sys.argv))
