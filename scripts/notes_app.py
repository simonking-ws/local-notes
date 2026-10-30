# -*- coding: utf-8 -*-
"""
素笺 —— 复刻 macOS 笔记（Apple Notes）的桌面应用（Python + Tkinter，零依赖，无需联网）。
运行：python scripts/notes_app.py
数据保存在项目根的 data/notes_data.db（打包成 exe 后是 exe 旁的 data/；旧数据首次启动会自动迁移）

分层：store.py（数据） → theme.py（配色） → widgets.py（控件）
      → app_ui.py / app_actions.py（界面 / 动作） → notes_app.py（入口）
"""
import os
import tkinter as tk

from store import (load_data, save_data, store, plain_of, _fix_sample_note,
                   _mark_sample_note, _upgrade_sample_note, _ensure_smart_folders,
                   normalize_folder_parents, smart_notes, subtree_ids, SMART_FOLDERS,
                   APP_NAME, SEAL_TITLE_MASK, set_save_error_hook,
                   dump_pending, pending_files)
from theme import PALETTE
from widgets import (SIDEBAR_W, LIST_W, SIDE_MIN, SIDE_MAX, LIST_MIN, LIST_MAX)
from app_ui import UIMixin
from app_actions import ActionsMixin
from md_conv import TODO_MARKS
import secret


class NotesApp(UIMixin, ActionsMixin):
    ROW_H = 66

    def __init__(self, root):
        self.root = root
        # 保存失败的处理状态。钩子必须先注册：下面几处「兼容旧数据」的 save_data
        # 都在界面建好之前执行，那时出错同样不能静默。
        self._save_error = None
        self._save_error_shown = False
        self._ui_ready = False
        set_save_error_hook(self._on_save_error)
        self.data = load_data()
        # 兼容旧数据：移除“我的 Mac”，并确保三个智能分类（全部 / 星标 / 加密）都在、
        # 名字是现行文案、且排在最前。智能分类是**筛选视图**，不是真实文件夹。
        self.data["folders"] = [f for f in self.data["folders"] if f["id"] != "f_mac"]
        if self.data.get("current_folder") == "f_mac":
            self.data["current_folder"] = "f_all"
        if _ensure_smart_folders(self.data):
            save_data(self.data)
        # 兼容脏数据：父引用悬空 / 成环 / 过深的一律提到根层（不清的话左栏渲染会漏行或递归爆栈）
        if normalize_folder_parents(self.data["folders"]):
            save_data(self.data)
        # 兼容旧数据：应用内置的示例笔记随应用改名（标题整串等于示例标题才认，
        # 用户自己改过标题的笔记不动）；顺带把正文里的“备忘录”措辞统一成“笔记”
        if _fix_sample_note(self.data):
            save_data(self.data)
        # 兼容旧数据：给内置示例笔记补上 sample 标记 → UI 里不可删除
        if _mark_sample_note(self.data):
            save_data(self.data)
        # 兼容旧数据：内置说明笔记补上「快捷键」一节（幂等：已有这一节就跳过）
        if _upgrade_sample_note(self.data):
            save_data(self.data)
        # 配色：优先 WorkBuddy 皮肤（PALETTE），解析失败时 PALETTE 即内置浅灰 LIGHT
        self.pal = PALETTE
        self._codes = []          # 正文里的代码块（换笔记时连同 Text 里的窗口一起销毁）
        self._hrs = []            # 正文里的分割线（同上）
        self._md_pending = None   # 空行刚转成标题/引用时记下的待补样式
        # 加密笔记：_keys 存「已解锁」的派生密钥（切走即清），_editor_note 记编辑器里
        # 当前载入的是哪一条 —— 换笔记时靠它把上一条重新封上。
        # _cur_pw 是「当前这篇加密笔记」的口令（只进内存不落库）：开下一篇时先拿它
        # 静默试一次，口令相同就不用再问（每篇 salt 不同，无法复用派生密钥）。
        self._keys = {}
        self._cur_pw = None
        self._editor_note = None
        self._code_family = None
        self._code_btn = None
        self._active_title = self._active_preview = self._active_date = None
        self.fmt_buttons = []
        self._star_buttons = {}
        self._tool_seps = []
        self._rows = {}
        self._folder_rows = {}
        self._folder_icons = {}
        self._folder_count_labels = {}
        self._folder_name_labels = {}
        self._drag = None
        self._drag_win = None
        self._drag_target = None
        # 多选状态：文件夹 / 笔记（Shift 区间、Ctrl 切换）
        self._folder_sel = set()
        self._folder_anchor = None
        self._note_sel = set()
        self._note_anchor = None
        # 全局设置（正文字号等），持久化在 notes_data.json 的 settings 里
        self.data.setdefault("settings", {})
        self.body_size = self._read_body_size()
        self.ui_font = self._read_ui_font()
        # 工具栏横向滑动状态（窗口变窄时按钮展示不全）
        self._tool_x = 0
        self._marquee = {}
        self._font_cache = {}      # 测量用字体（绑定本实例的 Tk 解释器）
        # 布局事务：折叠/展开这类「一改牵动全身」的操作期间抑制各处 Configure 联动，
        # 先一次性算完几何再统一刷新，避免中间态来回重排、重绘出可见的顿挫
        self._layout_txn = False
        self._vscroll_place_fn = None    # 编辑区滚动条重新定位（事务结束后统一调一次）
        # 延时任务登记表：主窗口销毁时统一取消，否则 Tk 会打印 invalid command name
        self._alive = True
        self._after_jobs = set()
        # 编辑去抖任务（见 SAVE_DEBOUNCE_MS / ROW_REFRESH_DEBOUNCE_MS）
        self._save_job = None
        self._row_job = None
        self._build_ui()
        self.apply_palette()
        if not self.data.get("current_note"):
            first = self.visible_notes()
            if first:
                self.data["current_note"] = first[0]["id"]
        self.refresh_all()
        # 界面就绪后，把「保存失败」这类必须让用户知道的事补提示出来。
        # 走 _after(0) 而不是直接弹：此时窗口还没完成首次绘制，模态框会盖在空窗口上。
        self._ui_ready = True
        # 上一次「自动更新」的收尾：批处理替换失败时会留一个日志，那时本进程
        # 已经退了、弹不出窗，只能下次启动再说。顺带清掉旧版本的安装包。
        self._after(300, self._check_update_aftermath)
        if self._save_error is not None:
            self._after(0, self._notify_save_error)
        else:
            self._after(0, self._check_pending)
        # 主窗口销毁时把挂起的延时任务全部取消
        try:
            self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        except Exception:
            pass

    def _on_root_destroy(self, e):
        if getattr(e, "widget", None) is not self.root:
            return
        self._alive = False
        # 关闭前把还挂在去抖里的改动落库；写不进去就把整份内存数据另存成快照。
        # 不静默吞掉：这一处出问题丢的是「整场会话的编辑」，而不是最后 600ms。
        self._save_job = None
        self._flush_on_exit()
        try:
            self._stop_all_marquee()
        except Exception:
            pass
        # 关闭数据库连接（否则进程退出时可能留下 -wal/-journal 文件）
        try:
            store().close()
        except Exception:
            pass
        for job in list(self._after_jobs):
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._after_jobs.clear()

    # ---------- 保存失败：让用户看得见，并留一份兜底快照 ----------
    def _on_save_error(self, err, data):
        """所有保存路径的统一出口（由 store.set_save_error_hook 注入）。

        退出阶段窗口正在销毁，弹窗已经没有意义，只记录；兜底由 _flush_on_exit 负责。
        """
        self._save_error = err
        if not getattr(self, "_alive", False) or not getattr(self, "_ui_ready", False):
            return
        if self._save_error_shown:
            return                      # 只提示一次，否则每 600ms 一次去抖保存都弹
        self._save_error_shown = True
        self._after(0, self._notify_save_error)

    def _notify_save_error(self):
        self._show_error(
            "保存失败",
            "数据没能写入数据库：\n%s\n\n"
            "修改仍保留在内存里，之后每次保存都会继续尝试。\n"
            "若反复失败，请先用「数据管理 → 导出备份」另存一份，不要直接关闭窗口。"
            % (self._save_error,))

    def _flush_on_exit(self):
        """退出前落库；失败就把内存数据写成 data/pending_*.json，避免整场编辑丢失。"""
        try:
            ok = save_data(self.data)
        except Exception:
            ok = False
        if ok is False:
            # 进程马上就退出，路径本身没人再读 —— 留个名字也没用，不接收返回值
            dump_pending(self.data, reason="退出时保存失败")
        return ok

    def _check_pending(self):
        """启动时检查上次保存失败留下的快照，提示一次后不再打扰。

        文件本身保留不动——它和「导出备份」同格式，用户随时能用「导入数据」捞回来。
        只在旁边放一个 .reported 空标记，避免每次启动都弹。
        """
        for path in pending_files():
            marker = path + ".reported"
            if os.path.exists(marker):
                continue
            try:
                with open(marker, "w", encoding="utf-8"):
                    pass
            except Exception:
                pass
            self._show_error(
                "发现未保存的数据",
                "上次运行时有数据没能写进数据库，已另存为：\n%s\n\n"
                "需要的话，可以在「数据管理 → 导入数据」里选择这个文件恢复。" % path)
            return

    # ---------- 延时任务（可集中取消） ----------
    def _after(self, ms, fn):
        """登记一个延时任务，返回 job id；窗口销毁后不再执行。"""
        job = [None]

        def run():
            self._after_jobs.discard(job[0])
            if self._alive:
                try:
                    fn()
                except tk.TclError:
                    pass

        job[0] = self.root.after(ms, run)
        self._after_jobs.add(job[0])
        return job[0]

    def _after_idle(self, fn):
        return self._after(0, fn)

    def _cancel_after(self, job):
        self._after_jobs.discard(job)
        try:
            self.root.after_cancel(job)
        except Exception:
            pass

    # ---------- 数据 ----------
    def folder_subtree(self, fid):
        """这个文件夹自己 + 全部子文件夹的 id（智能分类传回来的是它自己）。

        父级要连着子孙一起算：用户把笔记挪进子文件夹之后，父级的数字反而变小、
        父视图里也找不到那条笔记，看着就像数据丢了。
        """
        if fid in SMART_FOLDERS:
            return [fid]
        return subtree_ids(fid, self.data["folders"])

    def folder_notes(self, fid):
        """某个分类 / 文件夹（含子文件夹）下的笔记。

        三个智能分类（全部 / 星标 / 加密）只是**筛选条件**，筛选本身抽在
        store.smart_notes 里（纯函数，测试不建 GUI 也能断言）。
        """
        if fid in SMART_FOLDERS:
            return smart_notes(fid, self.data["notes"])
        ids = set(self.folder_subtree(fid))
        return [n for n in (self.data["notes"] or []) if n.get("folder") in ids]

    def visible_notes(self):
        q = (self.search_var.get() if hasattr(self, "search_var") else "").strip().lower()
        if q:
            # 搜索读的是库里的 plain 列；还有去抖中的改动就先落库，免得搜不到刚敲的字
            if self._save_job is not None:
                self._flush_save()
            # 搜索交给 SQLite：note 表里有 plain 列，直接在 SQL 里过滤 + 排序，
            # 不再把每条笔记的 content 逐字符拼成纯文本再比对（原来每敲一个字都全表扫）。
            idx = {n["id"]: n for n in self.data["notes"]}
            ids = store().search_ids(self.data["current_folder"], q,
                                     self.folder_subtree(self.data["current_folder"]))
            return [idx[i] for i in ids if i in idx]
        lst = self.folder_notes(self.data["current_folder"])
        return sorted(lst, key=lambda n: (0 if n.get("pinned") else 1,
                                          0 if n.get("starred", False) else 1,
                                          -n["updated"]))

    def plain(self, n):
        return plain_of(n)

    def _lines(self, n):
        """把笔记内容按换行拆成多行（待办框记作一个空格，代码块按自己的换行拆）。

        待办框现在是**字面字符**（⬜ / ✅，存量里还有旧版 ☐ / ⚪ / ▢ / ☑），这里把框字符本身
        丢掉 —— 侧栏的标题/预览要的是「人话」，塞个方框进去跟从前（窗口项记作
        空格）不一致。方框后面那个间隙是 NBSP，属于空白，strip() 会一并去掉。
        """
        lines = [""]
        for it in n["content"]:
            if it[0] == "t":
                for ch in it[1]:
                    if ch == "\n":
                        lines.append("")
                    elif ch in TODO_MARKS:
                        continue
                    else:
                        lines[-1] += ch
            elif it[0] == "w":
                lines[-1] += " "
            elif it[0] == "code":
                for ch in (it[2] if len(it) > 2 else ""):
                    if ch == "\n":
                        lines.append("")
                    else:
                        lines[-1] += ch
            elif it[0] == "hr":
                # 分割线：预览里当空行，不占文字（不写这一支的话它会被 else 静默吞掉，
                # 行为一样但看不出意图，将来加结构项容易漏）
                lines.append("")
        return lines

    def _sealed_title(self, n):
        """加密且未解锁时的列表标题：标题首字 + 一个星号（如「周*」）。

        老形态是清一色的「已加密笔记」—— 一排锁着的笔记长得一模一样，分不清
        谁是谁。露出首字正是为了让它们互相可区分，代价是泄漏一个字符（存在
        密文块里随密文走，见 secret.HINT_KEY）。升级前就加密的老笔记第一次
        解锁后才会补上首字，在那之前仍显示旧占位。
        """
        head = secret.hint_of(n.get("sec"))
        return (head + SEAL_TITLE_MASK) if head else "已加密笔记"

    def _is_sealed(self, n):
        """这篇加密笔记此刻在内存里是不是「锁着」的形态（没有可读的明文）。

        只看 content 空不空是不够的：content 里躺着东西也可能是不算数的
        （例如载入时被空编辑区覆盖过一次）。真正的判据是**手上有密钥吗** ——
        内容是我们渲染列表的依据，密钥才是「这篇已经开过」的凭据。
        """
        if not secret.is_blob(n.get("sec")):
            return False
        if not (n.get("content") or []):
            return True
        return not self._note_key(n["id"])

    def title_of(self, n):
        # 加密且未解锁：正文根本不在内存里，真标题也是加密的一部分，
        # 列表里只显示「首字 + 星号」的遮蔽形态。
        if self._is_sealed(n):
            return self._sealed_title(n)
        lines = self._lines(n)
        t = (lines[0] if lines else "").strip()
        return t or "新建笔记"

    def preview_of(self, n):
        if self._is_sealed(n):
            return "输入口令后查看内容"
        lines = self._lines(n)
        return (lines[1].strip() if len(lines) > 1 else "") or ""

    def fmt_date(self, ts):
        import time
        d = time.localtime(ts / 1000)
        n = time.localtime()
        if d.tm_yday == n.tm_yday and d.tm_year == n.tm_year:
            return time.strftime("%H:%M", d)
        if (n.tm_yday - d.tm_yday) == 1 and d.tm_year == n.tm_year:
            return "昨天"
        if d.tm_year == n.tm_year:
            return "%d月%d日" % (d.tm_mon, d.tm_mday)
        return "%d/%d/%d" % (d.tm_year, d.tm_mon, d.tm_mday)

    def trunc(self, s, n):
        s = (s or "").strip()
        return s if len(s) <= n else s[:n] + "…"

    def _date_text(self, n):
        """列表右侧时间：置顶显示「置顶」，否则按时间格式。"""
        return "置顶" if n.get("pinned") else self.fmt_date(n["updated"])


    def _read_body_size(self):
        try:
            sz = int(self.data.get("settings", {}).get("body_size", 12))
        except Exception:
            sz = 12
        return min(25, max(8, sz))

    def _read_side_w(self):
        """文件夹列宽度（像素）。拖过分隔条就记在 settings["side_w"]，没记就用默认 216。"""
        return self._read_col_w("side_w", SIDEBAR_W)

    def _read_list_w(self):
        """笔记列表列宽度（像素），同上，默认 300。"""
        return self._read_col_w("list_w", LIST_W)

    def _read_col_w(self, key, default):
        """读一栏宽度并夹进允许范围 —— 手改过 notes_data.json 也不会把界面撑坏。"""
        lo, hi = (SIDE_MIN, SIDE_MAX) if key == "side_w" else (LIST_MIN, LIST_MAX)
        try:
            w = int(self.data.get("settings", {}).get(key, default))
        except Exception:
            w = default
        return min(hi, max(lo, w))

    def _read_ui_font(self):
        """全局正文字体族（在「全局设置」里改）。空串表示跟随系统默认字体。

        这一项原来是工具栏上的一个字体下拉，但那是「给选中文字套字体」的选区分支用法，
        实际几乎没人用，却常年占着 93px 宽度；挪进设置后它就变成了正经的全局项。
        """
        try:
            fam = self.data.get("settings", {}).get("font_family", "") or ""
        except Exception:
            fam = ""
        return fam if isinstance(fam, str) else ""


    FONT_FAMILIES = [
        ("默认", None), ("微软雅黑", "Microsoft YaHei"), ("黑体", "SimHei"),
        ("宋体", "SimSun"), ("楷体", "KaiTi"), ("等宽 Consolas", "Consolas"),
    ]


    MARQUEE_MS = 33      # 定时器间隔（≈30fps）
    MARQUEE_PX = 1.0     # 每帧位移像素 → 约 30px/s，慢速可读；整像素步进不发虚


    # 行上约定三个字段：_name_box（固定宽盒子）、_full_name（完整文字）、
    # _name_clipped（是否已被截断）。滚动表以 key 区分：文件夹用 fid，笔记用 "n:"+nid。


    def current_note(self):
        nid = self.data.get("current_note")
        return next((n for n in self.data["notes"] if n["id"] == nid), None)


    def refresh_all(self):
        self.refresh_folders()
        self.refresh_notes()
        self.load_editor()


    # 行级（整行生效）样式标签。quote 也算行级：它同样是"这一整行属于某种块"，
    # 加入这里才能被 _sync_line_tags 补齐（在引用行末尾继续打字不会掉成正文）。
    LINE_TAGS = ("title", "h2", "body", "quote")


    EMPH_TAGS = ("bold", "italic", "bold_italic")


_SINGLETON_MUTEX = None


def _acquire_single_instance(name="Local\\SujianNotes_SingleInstance"):
    """单实例保护：用 Windows 命名互斥量，已有实例在跑就返回 False。

    为什么不用锁文件：锁文件在进程崩溃/被强杀时会留下来，下次启动得靠 pid 猜是否还活着，
    猜错就把人挡在外面。内核互斥量由系统在进程结束时自动释放，没有僵死状态。

    非 Windows 直接放行（本项目只在 Windows 上打包，源码跨平台时不做限制）。
    任何异常都放行——保护机制自己出问题，不该把用户挡在应用外面。
    """
    global _SINGLETON_MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle.restype = wintypes.BOOL
        handle = k32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(handle)
            return False
        _SINGLETON_MUTEX = handle               # 持有到进程退出，由系统回收
        return True
    except Exception:
        return True


def main():
    if not _acquire_single_instance():
        # 两个窗口共用一个库会互相覆盖，第二个直接拦掉，别再让它带着错误的数据跑起来
        warn = tk.Tk()
        warn.withdraw()
        try:
            from tkinter import messagebox
            messagebox.showwarning(
                APP_NAME,
                "素笺已经在运行了，请不要重复打开。\n\n"
                "两个窗口会同时改写同一个数据库，容易互相覆盖、丢失笔记。")
        except Exception:
            pass
        warn.destroy()
        return
    root = tk.Tk()
    NotesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
