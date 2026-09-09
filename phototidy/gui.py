"""GUI 入口(CustomTkinter):① 选择输入 → ② 预览确认 → ③ 开始整理(三区动线)。

仅为壳:全部业务逻辑走 core 层。界面原则:
- 默认先预览(总体原则 4),勾选确认后才执行移动
- 后台线程跑 core 任务,进度条 + 日志不卡界面
- 单文件失败不中断整体,最终汇总失败清单
- macOS 访问被拒时引导用户开启「完全磁盘访问」
"""

import dataclasses
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from . import db as db_module
from .config import Config, default_db_path
from .core import organizer, pipeline, scanner
from .logging import LOG_DIR, setup_file_logger

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少 GUI 依赖,请安装: pip install customtkinter\n")
    sys.exit(1)

log = logging.getLogger(__name__)

_NOTICE_FLAG = os.path.join(os.path.expanduser("~"), ".phototidy", ".seen_notice")

_TCC_HINT = (
    "无法访问所选目录(权限不足)。\n\n"
    "macOS 用户:请前往「系统设置 → 隐私与安全性 → 完全磁盘访问」,"
    "将本程序加入白名单后重试。\n"
    "(访问外置盘 / NAS / 系统目录时常见)"
)

# ---------- 设计令牌(参照 ChatGPT 桌面版:近白/近黑底、卡片化表面、细边框、单色主按钮) ----------
_PAD = 16  # 页面外边距
_GAP = 10  # 控件间距
_RADIUS = 12  # 卡片/按钮圆角
_ROW_H = 34  # 表格行高

_BG = ("#f9f9f9", "#171717")  # 窗口底色(浅色, 深色)
_CARD = ("#ffffff", "#202020")  # 卡片表面色
_BORDER = ("#e3e3e3", "#333333")  # 细边框
_TEXT = ("#0d0d0d", "#ececec")  # 主文字
_MUTED = ("#6f6f6f", "#a3a3a3")  # 次要文字
_SELECT = ("#f0f0f0", "#2a2a2a")  # 行 hover 底色
_PRIMARY = ("#0d0d0d", "#ececec")  # 主按钮底色
_PRIMARY_TXT = ("#ffffff", "#0d0d0d")
_PRIMARY_HOVER = ("#333333", "#f7f7f7")
_BADGE_DUP_BG = ("#fdeaea", "#4a2828")  # 「去重」徽标底色
_BADGE_DUP_FG = ("#b3261e", "#f2b8b5")
_BADGE_ORG_BG = ("#e8f0fd", "#25304a")  # 「归档」徽标底色
_BADGE_ORG_FG = ("#1a5fb4", "#a8c7fa")
_HINT_WARN = ("#b3261e", "#f2b8b5")  # 模式说明文字(风险语义,仅此一处用彩色)

_SCAN_LOG_EVERY = 100  # 扫描进度日志节流:每 100 个文件一条


class _ThrottledScanLog:
    """扫描进度回调节流:首条必发,此后每 _SCAN_LOG_EVERY 个文件一条,避免刷爆 UI 队列。"""

    def __init__(self, put, every: int = _SCAN_LOG_EVERY):
        self._put = put
        self._every = every
        self._n = 0

    def __call__(self, path: str) -> None:
        self._n += 1
        if self._n == 1 or self._n % self._every == 0:
            self._put(("log", f"扫描[{self._n}]: {path}"))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("拾光照片整理")
        self.geometry("1080x680")
        self.minsize(960, 600)
        ctk.set_appearance_mode("system")

        self.config = Config()
        self.db_path = default_db_path()
        self.folder = tk.StringVar(value="未选择源文件夹")
        self.output_folder = tk.StringVar(value="原地整理(不指定则在源文件夹内创建日期子目录)")
        self.mode = tk.StringVar(value=self.config.mode)
        self.plan: list = []
        self.skipped: set = set()
        self._queue = queue.Queue()
        self._worker = None
        self._move_exec_confirmed = False  # 移动模式执行前的一次性确认

        self._build_layout()
        self._first_run_notice()

    # ---------- 界面 ----------

    def _build_layout(self):
        self.configure(fg_color=_BG)

        # ---- ① 输入区:源文件夹 → 输出目录 → 整理方式 ----
        top = ctk.CTkFrame(
            self, fg_color=_CARD, corner_radius=_RADIUS, border_width=1, border_color=_BORDER
        )
        top.pack(fill="x", padx=_PAD, pady=(_PAD, _GAP))

        r1 = ctk.CTkFrame(top, fg_color="transparent")
        r1.pack(fill="x", padx=_GAP, pady=(_GAP, 4))
        ctk.CTkLabel(r1, text="① 选择照片文件夹", font=("", 13, "bold"), text_color=_TEXT).pack(
            side="left", padx=(0, _GAP)
        )
        ctk.CTkButton(
            r1,
            text="选择文件夹",
            width=96,
            height=30,
            corner_radius=_RADIUS,
            font=("", 13),
            fg_color=_CARD,
            hover_color=_SELECT,
            border_width=1,
            border_color=_BORDER,
            text_color=_TEXT,
            command=self._pick_folder,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            r1, textvariable=self.folder, anchor="w", text_color=_MUTED, font=("", 12)
        ).pack(side="left", fill="x", expand=True, padx=6)

        r2 = ctk.CTkFrame(top, fg_color="transparent")
        r2.pack(fill="x", padx=_GAP, pady=(0, 4))
        ctk.CTkLabel(r2, text="　 输出目录(可选)", font=("", 13, "bold"), text_color=_TEXT).pack(
            side="left", padx=(0, _GAP)
        )
        ctk.CTkButton(
            r2,
            text="选择目录",
            width=88,
            height=30,
            corner_radius=_RADIUS,
            font=("", 13),
            fg_color=_CARD,
            hover_color=_SELECT,
            border_width=1,
            border_color=_BORDER,
            text_color=_TEXT,
            command=self._pick_output_folder,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            r2, textvariable=self.output_folder, anchor="w", text_color=_MUTED, font=("", 12)
        ).pack(side="left", fill="x", expand=True, padx=6)

        r3 = ctk.CTkFrame(top, fg_color="transparent")
        r3.pack(fill="x", padx=_GAP, pady=(0, _GAP))
        ctk.CTkLabel(r3, text="　 整理方式", font=("", 13, "bold"), text_color=_TEXT).pack(
            side="left", padx=(0, _GAP)
        )
        # 模式切换:分段式 pill(替代原生下拉,提升质感),右侧随行说明
        seg = ctk.CTkFrame(
            r3, fg_color=_BG, corner_radius=_RADIUS, border_width=1, border_color=_BORDER
        )
        seg.pack(side="left")
        self._seg_buttons = {}
        for m, label in (("move", "移动"), ("copy", "复制")):
            b = ctk.CTkButton(
                seg,
                text=label,
                width=52,
                height=26,
                corner_radius=9,
                font=("", 12),
                command=lambda v=m: self._set_mode(v),
            )
            b.pack(side="left", padx=2, pady=2)
            self._seg_buttons[m] = b
        self.mode_hint = tk.StringVar()
        self.mode_hint_label = ctk.CTkLabel(
            r3, textvariable=self.mode_hint, anchor="w", text_color=_MUTED, font=("", 12)
        )
        self.mode_hint_label.pack(side="left", padx=_GAP)
        self._refresh_segmented()
        self._update_mode_hint()

        # ---- 自绘行列表卡片(行 = checkbox + 文件 + 动作徽标 + 目录 + 说明) ----
        table_wrap = ctk.CTkFrame(
            self, fg_color=_CARD, corner_radius=_RADIUS, border_width=1, border_color=_BORDER
        )
        table_wrap.pack(fill="both", expand=True, padx=_PAD, pady=(0, _GAP))

        tbl_title = ctk.CTkFrame(table_wrap, fg_color="transparent")
        tbl_title.pack(fill="x", padx=(_GAP, 0), pady=(_GAP, 0))
        ctk.CTkLabel(
            tbl_title, text="② 确认整理计划", font=("", 13, "bold"), text_color=_TEXT
        ).pack(side="left")
        ctk.CTkLabel(
            tbl_title, text="取消勾选可跳过个别文件", text_color=_MUTED, font=("", 12)
        ).pack(side="left", padx=8)

        # 表头(1px 分隔,列权重与下方行 grid 同步)
        thead = tk.Frame(table_wrap, bg=_CARD[0])
        thead.pack(fill="x", padx=6, pady=(6, 0))
        for ci, w in enumerate((0, 4, 0, 3, 3)):
            thead.grid_columnconfigure(ci, weight=w, uniform="pt")
        thead.grid_columnconfigure(2, minsize=84)
        tk.Label(thead, text="", bg=_CARD[0]).grid(row=0, column=0, padx=2)
        tk.Label(thead, text="源文件", bg=_CARD[0], fg=_MUTED[0], font=("", 11)).grid(
            row=0, column=1, sticky="w", padx=6, pady=(0, 4)
        )
        tk.Label(thead, text="动作", bg=_CARD[0], fg=_MUTED[0], font=("", 11)).grid(
            row=0, column=2, pady=(0, 4)
        )
        tk.Label(thead, text="目标目录", bg=_CARD[0], fg=_MUTED[0], font=("", 11)).grid(
            row=0, column=3, sticky="w", padx=6, pady=(0, 4)
        )
        tk.Label(thead, text="说明", bg=_CARD[0], fg=_MUTED[0], font=("", 11)).grid(
            row=0, column=4, sticky="w", padx=6, pady=(0, 4)
        )
        tk.Frame(table_wrap, bg=_BORDER[0], height=1).pack(fill="x", padx=6)

        # 行容器(CTk 原生滚动,行高固定 44px,0 间距)
        self.rows_frame = ctk.CTkScrollableFrame(
            table_wrap, fg_color="transparent", corner_radius=0, scrollbar_button_color=_MUTED
        )
        self.rows_frame.pack(fill="both", expand=True, padx=2, pady=(0, 4))
        self._row_widgets = []

        # ---- ③ 执行区:预览 + 红色执行按钮 + 进度 + 日志 ----
        bottom = ctk.CTkFrame(
            self, fg_color=_CARD, corner_radius=_RADIUS, border_width=1, border_color=_BORDER
        )
        bottom.pack(fill="x", padx=_PAD, pady=(0, _PAD))
        actions = ctk.CTkFrame(bottom, fg_color="transparent")
        actions.pack(fill="x", padx=_GAP, pady=(_GAP, 2))
        ctk.CTkLabel(actions, text="③ 开始整理", font=("", 13, "bold"), text_color=_TEXT).pack(
            side="left", padx=(0, _GAP)
        )
        ctk.CTkButton(
            actions,
            text="预览计划",
            width=96,
            height=34,
            corner_radius=_RADIUS,
            font=("", 13),
            fg_color=_CARD,
            hover_color=_SELECT,
            border_width=1,
            border_color=_BORDER,
            text_color=_TEXT,
            command=lambda: self._start_task(self._task_preview),
        ).pack(side="left", padx=(0, 4))
        self.btn_exec = ctk.CTkButton(
            actions,
            text="开始整理",
            width=220,
            height=34,
            corner_radius=_RADIUS,
            font=("", 13, "bold"),
            fg_color=_PRIMARY,
            text_color=_PRIMARY_TXT,
            hover_color=_PRIMARY_HOVER,
            state="disabled",
            command=self._on_exec_click,
        )
        self.btn_exec.pack(side="right")
        self.progress = ctk.CTkProgressBar(bottom, height=5, corner_radius=3)
        self.progress.pack(fill="x", padx=_GAP, pady=(_GAP, 4))
        self.progress.set(0)
        # 执行完成后显示,一键打开产物目录
        self._exec_dests: list = []
        # 计划 0 项时露出,供用户撤销性重置「已移出」状态
        self.btn_reset = ctk.CTkButton(
            bottom,
            text="重置已移出标记(后重预览)",
            height=26,
            font=("", 12),
            corner_radius=8,
            fg_color=_CARD,
            hover_color=_SELECT,
            border_width=1,
            border_color=_BORDER,
            text_color=_TEXT,
            command=self._reset_moved_for_folder,
        )
        self.btn_reset.pack_forget()
        self.btn_reveal = ctk.CTkButton(
            bottom,
            text="在输出目录中显示整理结果",
            height=26,
            font=("", 12),
            corner_radius=8,
            fg_color=_CARD,
            hover_color=_SELECT,
            border_width=1,
            border_color=_BORDER,
            text_color=_TEXT,
            command=lambda: self._reveal_in_finder(self._exec_dests),
        )
        self.btn_reveal.pack_forget()
        self.log_box = ctk.CTkTextbox(
            bottom,
            height=96,
            state="disabled",
            fg_color=_BG,
            corner_radius=_RADIUS,
            text_color=_MUTED,
            font=("Menlo", 11),
        )
        self.log_box.pack(fill="x", padx=_GAP, pady=(0, _GAP))

    def _refresh_segmented(self):
        """分段按钮当前模式高亮。"""
        for m, b in self._seg_buttons.items():
            on = m == self.mode.get()
            b.configure(fg_color=_CARD if on else "transparent", text_color=_TEXT if on else _MUTED)

    def _set_mode(self, v: str):
        self.mode.set(v)
        self.config.mode = v
        self._refresh_segmented()
        self._update_mode_hint()

    def _update_mode_hint(self):
        """pill 右侧一句话说清当前模式的后果;「移动」为风险语义,文字用警示色。"""
        if self.mode.get() == "copy":
            self.mode_hint.set("复制:先复制并校验哈希,校验通过后删除源图(多一层校验)")
            self.mode_hint_label.configure(text_color=_HINT_WARN)
        else:
            self.mode_hint.set("移动:源图将离开原文件夹,请先预览确认")
            self.mode_hint_label.configure(text_color=_HINT_WARN)

    def _update_exec_summary(self):
        """执行按钮文案携带数量摘要,让按钮自证「将要发生的事」。"""
        included = [it for i, it in enumerate(self.plan) if i not in self.skipped]
        if not included:
            self.btn_exec.configure(text="开始整理", state="disabled")
            return
        dup = sum(1 for it in included if it.action == "dedupe")
        org = len(included) - dup
        parts = []
        if org:
            parts.append(f"归档 {org}")
        if dup:
            parts.append(f"去重 {dup}")
        self.btn_exec.configure(
            text=f"开始整理 {len(included)} 项({'·'.join(parts)})", state="normal"
        )

    def _on_exec_click(self):
        """执行入口:两种模式都会移除源文件,会话内首次执行前弹一次确认。

        执行前在主线程快照 plan/skipped/mode,避免工作线程与勾选操作竞争。
        """
        mode = self.mode.get()
        if mode == "move" and not self._move_exec_confirmed:
            ok = messagebox.askokcancel(
                "确认执行移动",
                "当前为「移动」模式:整理后源文件将离开原文件夹。\n"
                "请先在上方预览表确认条目(取消勾选可跳过)。\n\n"
                "确定开始执行?「复制」模式同样会删除源文件,区别只是多一次校验。",
            )
            if not ok:
                return
            self._move_exec_confirmed = True
        plan = [item for i, item in enumerate(self.plan) if i not in self.skipped]
        skipped_n = len(self.skipped)
        self._start_task(self._task_execute, plan, len(self.plan), skipped_n, mode)

    def _first_run_notice(self):
        """首次使用安全提示:整理前建议先备份(仅提示一次)。"""
        if os.path.exists(_NOTICE_FLAG):
            return
        os.makedirs(os.path.dirname(_NOTICE_FLAG), exist_ok=True)
        with open(_NOTICE_FLAG, "w") as f:
            f.write("1")
        messagebox.showinfo(
            "首次使用提示",
            "整理操作会移动/删除源文件。\n请先「预览计划」确认无误,并建议提前备份源目录。",
        )

    def _pick_folder(self):
        path = filedialog.askdirectory(title="选择待整理的照片文件夹(整理结果直接放进它的子目录)")
        if path:
            self.folder.set(path)
            self.btn_reset.pack_forget()

    def _pick_output_folder(self):
        path = filedialog.askdirectory(title="选择整理结果的输出目录(不选则在源文件夹内原地整理)")
        if path:
            self.output_folder.set(path)

    def _resolved_archive_root(self, source_folder: str) -> str:
        """解析归档根目录:指定了输出目录时用输出目录,否则源文件夹(同 CLI 行为)。"""
        out = self.output_folder.get().strip()
        if out and not out.startswith("原地整理"):
            return out
        return source_folder

    def _reveal_in_finder(self, paths):
        """在系统文件管理器中打开第一个目标目录(macOS 访达 / Windows 资源管理器)。"""
        if not paths:
            return
        target = os.path.normpath(str(paths[0]))
        if not os.path.isdir(target):
            self._append_log(f"目标目录不存在: {target}")
            return
        try:
            if sys.platform == "win32":
                # Windows 用 os.startfile 打开资源管理器;explorer.exe 经 subprocess 传参
                # 不可靠(正斜杠会被当成命令行开关),且 explorer 是 GUI 程序不走控制台。
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.run(["open", target], check=False)
            else:
                subprocess.run(["xdg-open", target], check=False)
        except OSError as exc:  # 无桌面环境 / 缺 xdg-open
            self._append_log(f"无法打开文件管理器: {exc}")

    def _append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _toggle_skip(self, idx: int, var: tk.BooleanVar):
        """勾选/取消勾选行:勾选=纳入执行,取消=跳过。整行文字随之静音。"""
        included = var.get()
        if included:
            self.skipped.discard(idx)
        else:
            self.skipped.add(idx)
        row = self._row_widgets[idx]
        color = _TEXT if included else _MUTED
        for lbl in row["labels"]:
            lbl.configure(text_color=color)
        row["badge"].configure(
            fg_color=row["badge_bg"] if included else "transparent",
            text_color=row["badge_fg"] if included else _MUTED,
            border_color=_BORDER,
        )
        self._update_exec_summary()

    # ---------- 后台任务 ----------

    def _start_task(self, target, *args):
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("任务进行中", "请等待当前任务完成")
            return
        if not self.folder.get() or self.folder.get().startswith("未选择"):
            messagebox.showwarning("未选择目录", "请先选择要整理的文件夹")
            return
        self.progress.set(0)
        self._exec_dests = []
        self.btn_reveal.pack_forget()
        self.btn_reset.pack_forget()
        self._worker = threading.Thread(target=target, args=args, daemon=True)
        self._worker.start()
        self.after(80, self._poll_queue)

    def _poll_queue(self):
        """工作线程 → 主线程的 UI 更新队列。"""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self.progress.set(payload)
                elif kind == "plan_done":
                    self._fill_plan(payload)
                    if payload:
                        self.btn_reset.pack_forget()
                elif kind == "exec_done":
                    msg, moved = payload
                    self._append_log(msg)
                    self._clear_plan_after_exec(moved)
                    if self._exec_dests:
                        self.btn_reveal.pack(fill="x", padx=_GAP, pady=(0, 2))
                    return
                elif kind == "done":
                    self._append_log(payload)
                    self._update_exec_summary()
                    if self._exec_dests:
                        self.btn_reveal.pack(fill="x", padx=_GAP, pady=(0, 2))
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _clear_plan_after_exec(self, moved: int):
        """执行全部成功后清空计划列表,② 区显示完成态,执行按钮随之禁用。"""
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self._row_widgets = []
        self.plan, self.skipped = [], set()
        hint = ctk.CTkLabel(
            self.rows_frame,
            text=(
                f"整理完成:已处理 {moved} 项,源文件已移入目标目录。\n"
                "可点下方「在输出目录中显示整理结果」查看;再点【预览计划】可复查是否还有可整理项。"
            ),
            justify="left",
            anchor="w",
            text_color=_MUTED,
            font=("", 12),
        )
        hint.pack(fill="x", padx=20, pady=20)
        self._update_exec_summary()

    def _fill_plan(self, plan):
        """渲染计划为自绘行:checkbox + 图标 + 文件名 + 动作徽标 + 目录 + 说明。"""
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self._row_widgets = []
        self.plan, self.skipped = plan, set()
        for i, item in enumerate(plan):
            self._add_row(i, item)
        self._update_exec_summary()
        if not plan:
            self._show_empty_plan_hint()

    def _show_empty_plan_hint(self):
        """plan 为空时,在表格区给引导文案,同时露出「重置已移出标记」按钮。"""
        has_folder = not self.folder.get().startswith("未选择")
        msg = (
            "计划为 0 项。可能原因:\n"
            "• 所选文件夹此前已执行过,全部归档/去重完成\n"
            "• 文件被移回原位但数据库仍标记为「已移出」\n\n"
            "如确认文件已拉回,点下方【重置已移出标记】再重预览。"
            if has_folder
            else "请先选择文件夹,再点【预览计划】。"
        )
        hint = ctk.CTkLabel(
            self.rows_frame,
            text=msg,
            justify="left",
            anchor="w",
            text_color=_MUTED,
            font=("", 12),
        )
        hint.pack(fill="x", padx=20, pady=20)
        if has_folder:
            self.btn_reset.pack(fill="x", padx=_GAP, pady=(0, 2))

    def _add_row(self, idx: int, item):
        row = ctk.CTkFrame(self.rows_frame, fg_color="transparent", corner_radius=8, height=42)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        # 列权重与表头一致(5 列)
        for ci, w in enumerate((0, 4, 0, 3, 3)):
            row.grid_columnconfigure(ci, weight=w, uniform="pt")
        row.grid_columnconfigure(2, minsize=84)

        is_dup = item.action == "dedupe"
        badge_bg = _BADGE_DUP_BG if is_dup else _BADGE_ORG_BG
        badge_fg = _BADGE_DUP_FG if is_dup else _BADGE_ORG_FG
        badge_txt = "去重" if is_dup else "归档"

        var = tk.BooleanVar(value=True)
        cb = ctk.CTkCheckBox(
            row,
            text="",
            width=20,
            height=20,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            fg_color=_PRIMARY,
            hover_color=_PRIMARY_HOVER,
            border_color=_BORDER,
            variable=var,
            command=lambda i=idx, v=var: self._toggle_skip(i, v),
        )
        cb.grid(row=0, column=0, padx=(8, 2), pady=8)

        name = ctk.CTkLabel(
            row, text=os.path.basename(item.src), anchor="w", font=("", 13), text_color=_TEXT
        )
        name.grid(row=0, column=1, sticky="ew", padx=6)

        badge = ctk.CTkLabel(
            row,
            text=badge_txt,
            width=48,
            height=20,
            corner_radius=10,
            font=("", 11),
            fg_color=badge_bg,
            text_color=badge_fg,
        )
        badge.grid(row=0, column=2, pady=8)

        dest = ctk.CTkLabel(row, text=item.dest_dir, anchor="w", font=("", 12), text_color=_TEXT)
        dest.grid(row=0, column=3, sticky="ew", padx=6)

        reason = ctk.CTkLabel(row, text=item.reason, anchor="w", font=("", 12), text_color=_MUTED)
        reason.grid(row=0, column=4, sticky="ew", padx=6)

        # hover:浅色背景浮现
        def on_enter(_e, r=row):
            r.configure(fg_color=_SELECT)

        def on_leave(_e, r=row):
            r.configure(fg_color="transparent")

        for w in (row, name, dest, reason, badge):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        self._row_widgets.append(
            {
                "labels": (name, dest, reason),
                "badge": badge,
                "badge_bg": badge_bg,
                "badge_fg": badge_fg,
            }
        )

    def _task_preview(self):
        try:
            folder = self.folder.get()
            self._log_target_summary()
            self._queue.put(("log", "开始扫描入库…"))
            result = scanner.scan(
                folder,
                self.db_path,
                self.config,
                progress_cb=_ThrottledScanLog(self._queue.put),
            )
            self._queue.put(
                ("log", f"扫描完成: 入库 {result['scanned']}, 跳过 {result['skipped_no_time']}")
            )
            root = self._resolved_archive_root(folder)
            # 与 CLI run 共用同一条流水线,保证两端计划一致
            dup_plan, org_plan = pipeline.build_plan(self.db_path, folder, root, self.config)
            plan = dup_plan + org_plan
            self._queue.put(("plan_done", plan))
            self._queue.put(
                (
                    "done",
                    (
                        f"计划生成: 重复移出 {len(dup_plan)} 项, 归档 {len(org_plan)} 项。"
                        "取消勾选左侧复选框可跳过个别文件。"
                    ),
                )
            )
        except PermissionError:
            self._queue.put(("done", "权限不足,已中止"))
            self._queue.put(("log", _TCC_HINT))
        except Exception as exc:
            log.exception("预览失败")
            self._queue.put(("done", f"预览失败: {exc}"))

    def _task_execute(self, plan, total_n, skipped_n, mode):
        """plan/skipped/mode 均为主线程快照,本线程内不再读任何 tk 状态。"""
        total = max(len(plan), 1)
        counter = {"n": 0}

        def cb(_item, _ok, _err):
            counter["n"] += 1
            self._queue.put(("progress", counter["n"] / total))

        try:
            mode_label = "复制(校验后删源)" if mode == "copy" else "移动(源移除)"
            self._queue.put(
                (
                    "log",
                    f"开始执行 {len(plan)}/{total_n} 项(跳过 {skipped_n} 项,模式: {mode_label})…",
                )
            )
            cfg = dataclasses.replace(self.config, mode=mode)
            result = organizer.execute(plan, self.db_path, cfg, progress_cb=cb)
            dests = sorted({item.dest_dir for item in plan})
            self._exec_dests = [d for d in dests if os.path.isdir(d)]
            msg = f"{mode_label}完成: {result['moved']} 项"
            if self._exec_dests:
                msg += "\n整理结果位于:\n" + "\n".join(f"  {d}" for d in self._exec_dests)
            if result["failed"]:
                msg += f"\n失败 {len(result['failed'])} 项:\n" + "\n".join(
                    f"  {f['src']}: {f['error']}" for f in result["failed"]
                )
                msg += f"\n详细错误见日志: {getattr(self, 'log_path', LOG_DIR)}"
                # 有失败项:保留计划列表便于核对
                self._queue.put(("done", msg))
            else:
                # 全部成功:清空计划列表,② 区切换为「整理完成」状态
                self._queue.put(("exec_done", (msg, result["moved"])))
        except PermissionError:
            self._queue.put(("done", "权限不足,已中止"))
            self._queue.put(("log", _TCC_HINT))
        except Exception as exc:
            log.exception("执行失败")
            self._queue.put(("done", f"执行失败: {exc}"))

    # ---------- 撤销性重置:文件被手动拉回后,撤销 OLD 的「已移出」标记 ----------

    def _reset_moved_for_folder(self):
        if not self.folder.get() or self.folder.get().startswith("未选择"):
            messagebox.showwarning("未选择目录", "请先选择要整理的文件夹")
            return
        folder = self.folder.get()
        try:
            with db_module.connect(self.db_path) as conn:
                result = db_module.reset_moved_under(conn, folder)
        except Exception as exc:
            log.exception("重置已移出标记失败")
            messagebox.showerror("重置失败", f"重置已移出标记失败: {exc}")
            return
        reset = result["reset"]
        skipped = result["skipped_missing"]
        msg = f"已重置 {reset} 项「已移出」标记"
        if skipped:
            msg += f",跳过 {skipped} 项(磁盘上不存在,保留 moved=1)"
        messagebox.showinfo("重置完成", msg + "\n\n请重新点击【预览计划】生成最新计划。")
        self._append_log(f"重置完成: {msg}")
        self.btn_reset.pack_forget()

    def _log_target_summary(self) -> None:
        root = self._resolved_archive_root(self.folder.get())
        is_orig = root == self.folder.get()
        mode_label = (
            "复制(校验哈希后删除源文件)" if self.mode.get() == "copy" else "移动(源文件会改变位置)"
        )
        headline = "整理目标:源文件夹内原地整理" if is_orig else f"整理目标:输出目录 {root}"
        self._queue.put(("log", headline + f"|模式: {mode_label}"))


def entry() -> None:
    log_path = setup_file_logger()
    if "--selftest" in sys.argv:  # 打包冒烟:预先标记已读首次提示,避免 messagebox 阻塞
        os.makedirs(os.path.dirname(_NOTICE_FLAG), exist_ok=True)
        with open(_NOTICE_FLAG, "w") as f:
            f.write("1")
    app = App()
    app.log_path = log_path  # 供执行失败消息指向具体日志文件
    if "--selftest" in sys.argv:  # 打包冒烟:实例化后销毁,不进主循环
        app.update()
        app.destroy()
        log.info("selftest ok")
        sys.exit(0)
    app._append_log(f"日志: {log_path}")
    app.mainloop()


if __name__ == "__main__":
    entry()
