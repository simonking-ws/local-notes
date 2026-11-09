# -*- coding: utf-8 -*-
"""素笺 —— 动作层：新建 / 删除 / 星标 / 导入导出 / 编辑排版 / 序列化落库。

以 mixin 形态提供，最终由 notes_app.py 里的 class NotesApp(UIMixin, ActionsMixin) 组合。
"""
import os
import re
import json
import time
import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import dpi
import win_frame

from store import (APP_NAME, uid, _t, seed, store, save_data,
                   SMART_FOLDERS, SYSTEM_FOLDER, RECYCLE_FOLDER, RECYCLE_TTL_DAYS,
                   pick_note_color, _ensure_note_colors,
                   _note_head,
                   ALARM_INDEX_ID, ALARM_CELL_TAG,
                   folder_children, folder_depth, folder_ancestors,
                   recycle_item_view,
                   set_all_collapsed, subtree_ids, MAX_FOLDER_DEPTH)
from theme import FONT, on_accent
from widgets import (CodeBlock, HRule, AlarmCard, PopupMenu, MENU_DANGER,
                     DLG_TEXT_W, SAVE_DEBOUNCE_MS, ROW_REFRESH_DEBOUNCE_MS,
                     _popover_bg, _popover_border, _mix_hex)
from md_conv import (detect_prefix, find_urls, find_time_call, is_hr, parse_fence,
                     MD_LINK_RE,
                     LI_INDENT, BULLET_MARK, NUM_MARK, TRI_MARK, split_list_prefix,
                     LIST_KINDS, list_marker, renumber_prefix,
                     TODO_OFF, TODO_LEGACY, TODO_MARKS, TODO_GAP, TODO_GAP_TAG, _MARK_NBSP)
import exporter
import pdf_export
import secret
import backup
import calc
import alarm
import imgstore
import piclist
from mention import find_mention, note_url, note_id_of_url, mention_text, rank
from mention_ui import MentionPopup
from doodle import DoodleLayer


# 导出格式：(键, 对话框里的名字, 扩展名)
EXPORT_KINDS = (
    ("txt", "纯文本", "txt"),
    ("md", "Markdown", "md"),
    ("pdf", "PDF", "pdf"),
)


# 链接标签的前缀：每个链接一个独立 tag（lk_1 / lk_2 …），URL 存在笔记的 links 里。
# 为什么不在正文里保留 `[文字](url)` 再把它隐藏起来：隐藏要靠 elide，光标会在
# 隐藏区里跳来跳去，删除/粘贴都会把括号吐回来。tag 跟着字符走，编辑起来才自然。
LINK_TAG_PREFIX = "lk_"

# 整行图片语法「下载中」的占位文字与标签。**模块级**而不是只挂在类上：
# serialize() 里 _is_ephemeral_tag 要在测试用的假 app 上也能跑（那些 harness
# 只混入方法、没有类属性），用 self.XXX 会 AttributeError。
IMG_LOADING_TAG = "img_loading"
IMG_LOADING_TEXT = "图片加载中…"



class ActionsMixin(object):
    def export_data(self):
        """把文件夹 + 笔记 + 外观偏好导出成一份备份（backup.py 的 schema 2）。

        默认导出 `.json.gz`（沿用一直以来的文件格式）：整份是真 gzip，解压后
        是**编码容器** —— 里面只有一串 data，看不到笔记正文；导入时程序按文件头
        自动还原，不需要用户记开关。口令可以留空（留空就是纯编码、谁都能导回来）；
        填了口令才是真加密，导入时要输同一个口令。明文 `.plain.json` 只作为
        调试 / 人肉改数据的显式选项保留。

        格式本身（版本闸门、校验和、正文压缩、prefs/state 分离）全在 backup.py，
        这里不拼 JSON：导出端和导入端必须是同一套定义，各写一半就会重演
        「导出的字段导入不采纳」那种事。
        """
        default = "%s备份_%s.json.gz" % (
            APP_NAME, datetime.datetime.now().strftime("%Y%m%d-%H%M"))
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出备份", defaultextension=".json.gz",
            initialfile=default, initialdir=os.path.expanduser("~"),
            filetypes=[("素笺备份（编码 / 加密，压缩）", "*.json.gz"),
                       ("明文 JSON（调试 / 手工修改）", "*.plain.json"),
                       ("所有文件", "*.*")])
        if not path:
            return
        plain = str(path).lower().endswith(".plain.json")
        password = ""
        if not plain:
            pw = self._ask_password(
                "导出备份",
                "备份内容会被编码：解压后打开也看不到笔记正文，\n"
                "导入时程序会自动还原。\n\n"
                "直接点确定 = 不设口令（推荐）；\n"
                "填写口令 = 额外加密，导入时需要它，忘记后无法恢复。",
                allow_empty=True)
            if pw is None:
                return
            password = pw
        try:
            # 图片本体（data/media/）随备份带走：正文只存相对 src，
            # 换机 / 存档时没有这些文件，图就全空了。
            payload = backup.encode(self.data, extra={
                "media": backup.pack_media(self.data.get("notes") or [])})
            nbytes = (backup.dump(path, payload) if plain else
                      backup.dump_container(path, payload, password=password))
        except Exception as e:
            self._show_error("导出失败", "写入文件失败：\n%s" % e)
            return
        kind = "明文 JSON" if plain else ("已加密" if password else "已编码")
        # 外观是不是真的带上了，如实报告：没配过就一个字都不提，别让人以为存了
        nprefs = sum(1 for k in backup.PREFS_KEYS
                     if k in (self.data.get("settings") or {}))
        tail = "，含 %d 项外观设置" % nprefs if nprefs else ""
        # 数量按**实际导出**的算，不是库里的条数：内置说明笔记不进备份（见 backup.encode），
        # 拿 len(self.data["notes"]) 报数会多出 1 条，用户去文件里数却对不上。
        self._show_info("导出完成",
                        "已导出 %d 个文件夹、%d 条笔记%s\n%s · %.0f KB\n%s" %
                        (len(payload["folders"]), len(payload["notes"]), tail,
                         kind, nbytes / 1024.0, path))

    # ---------- 导出当前笔记（纯文本 / Markdown / PDF） ----------

    def export_note(self, kind):
        """把**当前这篇笔记**导出成 txt / md / pdf。

        正文取的是「正在编辑器里的那一份」（`serialize()`），不是库里那一份 ——
        输完字 0.5 秒内就点导出的话，去抖保存还没落库，直接读库会少掉最后几个字。
        加密且未解锁的笔记一律拒绝：编辑器里躺的是「文字已加密」这句占位话，
        导出它等于导出一篇假的空笔记。
        """
        n = self.current_note()
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        if getattr(self, "_locked_view", False) or self._is_sealed(n):
            self._show_info("提示", "这篇笔记已加密，输入口令解锁后才能导出。")
            return
        label, ext = next(((lab, e) for k, lab, e in EXPORT_KINDS if k == kind),
                          ("纯文本", "txt"))
        if kind == "pdf" and not pdf_export.available():
            self._show_error("无法导出 PDF",
                             "PDF 导出依赖系统自带的 GDI 排版，目前只在 Windows 上可用。\n"
                             "可以改用「导出为纯文本 / Markdown」。")
            return
        runs = n.get("content") or []
        if getattr(self, "_editor_note", None) == n["id"]:
            try:
                live = self.serialize()
            except Exception:
                live = None
            if live:
                runs = live
        title = self.title_of(n)
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出为%s" % label,
            defaultextension="." + ext,
            initialfile=exporter.safe_filename(title, ext),
            initialdir=os.path.expanduser("~"),
            filetypes=[("%s 文件" % label, "*." + ext), ("所有文件", "*.*")])
        if not path:
            return
        blocks = exporter.blocks_from_content(runs)
        try:
            if kind == "pdf":
                pages, nbytes = pdf_export.export_pdf(
                    path, blocks, title, self.pal, self.body_size,
                    getattr(self, "ui_font", None) or FONT[0],
                    self.code_font()[0])
                extra = "%d 页 · %.0f KB" % (pages, nbytes / 1024.0)
            else:
                text = (exporter.plain_text(blocks) if kind == "txt"
                        else exporter.markdown_text(blocks, n.get("links") or {}))
                # txt 带 BOM：Windows 记事本对无 BOM 的 UTF-8 会按 ANSI 猜，中文直接乱码
                with open(path, "w", encoding=("utf-8-sig" if kind == "txt" else "utf-8"),
                          newline="\r\n" if kind == "txt" else "\n") as f:
                    f.write(text)
                extra = "%d 行 · %.0f KB" % (text.count("\n"),
                                            len(text.encode("utf-8")) / 1024.0)
        except Exception as e:
            self._show_error("导出失败", "写入 %s 文件时出错：\n%s" % (label, e))
            return
        self._show_info("导出完成", "已导出为 %s（%s）\n%s" % (label, extra, path))

    def import_data(self):
        """从备份导入：schema 1 / 2、压缩 / 明文 / 编码 / 加密都认；可选覆盖或合并。

        编码态（空口令导出）在 backup.load 里按文件头自动剥壳还原，用户零感知；
        只有加密态会抛 NeedPassword，这里才弹一次口令框。旧备份、pending 兜底
        快照同样走原来的路径。解码在 backup.decode，落库整理在
        backup.overwrite / backup.merge —— 这里只负责问路和收尾。
        """
        path = filedialog.askopenfilename(
            parent=self.root, title="选择备份文件", initialdir=os.path.expanduser("~"),
            filetypes=[("素笺备份", "*.json *.json.gz *.gz *.sjbak"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            raw = backup.load(path)
            d, warnings = backup.decode(raw)
        except backup.NeedPassword:
            pw = self._ask_password(
                "导入备份", "这份备份设了口令，请输入导出时用的口令：")
            if pw is None:
                return
            try:
                raw = backup.load(path, password=pw)
                d, warnings = backup.decode(raw)
            except backup.BackupError as e:
                self._show_error("导入失败", str(e))
                return
            except Exception as e:
                self._show_error("导入失败", "文件读不出来：\n%s" % e)
                return
        except backup.BackupError as e:
            self._show_error("导入失败", str(e))
            return
        except Exception as e:
            self._show_error("导入失败", "文件读不出来：\n%s" % e)
            return
        # 校验和不符只警告、不硬拦：用户手改过备份是合法需求，但必须知道「不保证完整」
        if warnings and not self._ask_confirm(
                "备份文件可能不完整", "\n\n".join(warnings) + "\n\n仍然继续导入吗？",
                ok_text="继续导入", danger=True):
            return
        content_line, when = backup.summary(d)
        mode = self._ask_import_mode(content_line, when)
        if not mode:
            return
        # 图片本体写回 data/media/（在写库前：正文会引用这些相对路径）。
        # 校验和不符时上面的确认已经问过；这里只管把带回来的资源落地。
        if isinstance(raw, dict) and isinstance(raw.get("media"), dict):
            backup.unpack_media(raw["media"])
        before = len(self.data["notes"])
        new = (backup.overwrite(d, self.data) if mode == "overwrite"
               else backup.merge(d, self.data))
        # 备份里没带颜色的（老备份 / 别的工具造的）在落库前一次补齐：
        # 放到界面渲染时补的话，颜色只留在内存里，下次启动重新分配还可能换色
        _ensure_note_colors(new)
        try:
            store().reset(new)
        except Exception as e:
            self._show_error("导入失败", "写入数据库失败：\n%s" % e)
            return
        self._apply_new_data(new)
        tail = ""
        if mode == "merge":
            # 按正文指纹去重：内容相同的笔记不会再被追加一次，这里如实报告跳过多少
            tail = "\n新增 %d 条（与现有内容相同的已跳过）" % (
                len(new["notes"]) - before)
        elif d.get("prefs"):
            tail = "\n外观设置已按备份恢复"
        self._show_info("导入完成",
                        "导入方式：%s\n现有 %d 个文件夹、%d 条笔记%s" %
                        ("覆盖" if mode == "overwrite" else "合并",
                         len(new["folders"]), len(new["notes"]), tail))

    def clear_all_data(self):
        """清空所有数据并回到初次打开的状态（工作 / 个人 + 系统 + 智能分类 + 示例笔记）。"""
        # 系统区不在「自建文件夹」之列：清空后它照样在（seed 里带着），
        # 统计里不能算进去，否则弹窗说删 3 个、清完还剩 1 个。
        mine = [f for f in self.data["folders"]
                if not f.get("smart") and f.get("id") != SYSTEM_FOLDER]
        n_notes = len(self.data["notes"])
        if not self._ask_confirm(
                "清空数据",
                "确定要清空所有数据吗？\n\n"
                "将删除 %d 个自建文件夹、%d 条笔记。" % (len(mine), n_notes),
                ok_text="清空", danger=True):
            return
        if not self._ask_confirm(
                "再次确认",
                "这是最后一次确认：\n"
                "所有笔记与文件夹都会被永久删除，无法恢复，\n"
                "程序会回到初次打开时的状态：\n"
                "「工作」「个人」两个文件夹，加上自带的「系统」。\n\n"
                "确认清空？",
                ok_text="确认清空", danger=True):
            return
        new = seed()
        _ensure_note_colors(new)      # 示例笔记也要有各自的色条
        store().reset(new)
        self._apply_new_data(new)
        self._show_info("已完成", "数据已清空，界面已恢复到初始状态。")

    def apply_color(self, hexv):
        """仅对当前选中的文字设置颜色；未选中任何文字则提示，不改动。"""
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("提示", "请先选中要设置颜色的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        for t in list(self.text.tag_names()):
            if t.startswith("c_"):
                self.text.tag_remove(t, a, b)
        tag = "c_" + str(hexv).lstrip("#").lower()
        self.data.setdefault("styles", {})[tag] = {"fg": hexv}
        self.text.tag_configure(tag, foreground=hexv)
        self.text.tag_add(tag, a, b)
        # 注意：这里不再设置下划线颜色。Tk 默认「未指定 underlinefg 时下划线的颜色 = 文字颜色」，
        # 所以下划线自动跟随文字颜色；而通过「U 颜色」显式设置的下划线颜色也不会被文字颜色覆盖。
        self.mark_modified()

    def _apply_underline_color_range(self, a, b, hexv):
        """给区间设置下划线颜色。

        关键：Tk 要求 `-underline` 与 `-underlinefg` 必须写在**同一个 tag** 上，
        下划线才会用该颜色绘制；分成两个 tag（一个只给 underlinefg）时颜色不生效。
        """
        for t in list(self.text.tag_names()):
            if t.startswith("u_"):
                self.text.tag_remove(t, a, b)
        tag = "u_" + str(hexv).lstrip("#").lower()
        self.data.setdefault("styles", {})[tag] = {"ufg": hexv}
        self.text.tag_configure(tag, underline=True, underlinefg=hexv)
        self.text.tag_add(tag, a, b)

    def apply_underline_color(self, hexv):
        """仅对选中文字设置「下划线颜色」（underlinefg），与文字颜色互相独立。"""
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("提示", "请先选中要设置下划线颜色的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        # u_ 标签自带 underline=True，因此无需再单独加 underline 标签
        self._apply_underline_color_range(a, b, hexv)
        self.mark_modified()

    def reset_format(self):
        """把当前笔记恢复为默认格式：清空行内样式（加粗/颜色/字体/删除线等），
        但保留行级标签（title/h2/body）——标题仍还原为标题。纯文本与待办框保留。"""
        n = self.current_note()
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        if not self._ask_confirm(
                "还原默认格式",
                "确定将当前笔记恢复为默认格式吗？\n加粗、颜色、字体、删除线等行内样式会被清除，"
                "标题/正文格式会保留。", ok_text="还原"):
            return
        # 只清除行内样式标签，保留行级标签 title/h2/body/quote（代码块和分割线
        # 没有样式标签，跳过）。
        # todo / todo_done / todo_gap 也要留：待办框现在是**字面字符**，它的颜色、
        # 字号、勾选后的底色和点击切换全靠这几个标签 —— 一并清掉的话方框变灰、
        # 变回小号，而且点不动了。
        keep = {"title", "h2", "body", "quote", "todo", "todo_done", TODO_GAP_TAG}
        for it in n["content"]:
            if len(it) > 2 and it[0] in ("t", "w"):
                it[2] = [t for t in it[2] if t in keep]
        # 行内样式里也包含富链接的 lk_N：清了标签，登记表里那条 URL 就成了孤儿
        n.pop("links", None)
        save_data(self.data)
        self.load_editor()
        self._update_active_row()

    def _move_note_to_folder(self, nid, fid):
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            return
        if self.is_protected_note(n):
            # 兜底（拖拽入口已经拦过）：系统笔记钉在自己的视图里，别处留不住它
            return
        n["folder"] = fid
        n["updated"] = __import__("time").time() * 1000
        n["order"] = self.top_note_order(n)
        save_data(self.data)
        # 就地刷新列表与所有文件夹计数（不重建文件夹行 → 不闪烁）。
        # ⚠️ 计数徽章是 _refresh_folder_counts 里的 CountBadge（Canvas 自绘的圆 /
        # 胶囊），**没有 -text 选项**：早先这里手写 lbl.configure(text=...) 会抛
        # TclError，拖拽移动和右键移动都断在这一步（后面的选中收口根本没跑）。
        self.refresh_notes()
        self._refresh_folder_counts()

    # ---------- 右键「移动到文件夹…」 ----------

    def _note_move_targets(self, exclude=()):
        """这一篇笔记可以移去哪些文件夹，返回文件夹字典列表（不是 id）。

        智能分类是筛出来的视图、「系统」只装内置说明，两处都留不住普通笔记；
        单篇移动时再把自己当前的「家」排掉 —— 选它等于什么都没发生，还会
        白白改一遍 updated / order，把这篇顶到列表最前面。
        """
        excl = set(exclude)
        return [f for f in self.data["folders"]
                if not f.get("smart") and f["id"] not in SMART_FOLDERS
                and f["id"] != SYSTEM_FOLDER and f["id"] not in excl]

    def _popup_move_note(self, nid):
        """右键 → 从整棵文件夹树里挑一个目标，把这一篇搬过去。

        与拖拽的分工：拖拽是「左栏看得见就顺手一扔」，收着的子层得先逐级展开；
        这里直接用 `_ask_choose_folder` 的树，收起来的分支在弹窗里也能展开选。
        真正改数据仍走 `_move_note_to_folder`（同一套落库 / 计数 / 刷新），
        收口走 `_select_after_move` —— 移动后默认选中这一条，在当前视图里看不见
        就跟着切到新家，和拖拽落定后的手感一致。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n or self.is_protected_note(n):
            return
        targets = self._note_move_targets(exclude=[n.get("folder")])
        if not targets:
            self._show_info("提示", "没有其他文件夹可以移入。")
            return
        title = self.trunc(self.title_of(n), 24)
        tid = self._ask_choose_folder("移动到文件夹",
                                      "把「%s」移动到：" % title, targets)
        if not tid or tid == n.get("folder"):
            return
        self._move_note_to_folder(nid, tid)
        self._select_after_move(nid)

    def _popup(self, event, nid):
        # getattr 兜底：老的单测用 _Stub 只实现动作层的一部分方法
        if getattr(self, "_recycle_view", lambda: False)():
            # 回收站里的 id 是回收记录 id，不是笔记 id —— 绝不能落进下面那条
            # `data["notes"]` 查找（万一撞上同 id 的真笔记，菜单就全错了）。
            if nid not in self._note_sel:
                self._note_sel = {nid}
                self._note_anchor = nid
                self._restyle_notes()
            m = PopupMenu(self.root, self.pal)
            # 单条口径：只留「对这一条」的两个动作。批量口味（还原全部、清空回收站）
            # 归左下角那枚回收站入口的右键菜单 —— 在单条上给出「清空回收站」太容易
            # 点错，一次连带清掉别的项目。
            if len(self._note_sel) > 1:
                # 多选后右键（Shift/Ctrl 点出来的）：「清空」必须跟着选中集合走，
                # 否则一次只能清一条，选了半天等于白选。文案带数量，点下去之前
                # 就知道会动几项。
                cnt = len(self._note_sel)
                m.add("还原选中的 %d 项" % cnt, icon="move",
                      command=lambda: self.recycle_restore())
                m.add("清空选中的 %d 项" % cnt, icon="trash", danger=True,
                      command=lambda: self.recycle_purge_items(
                          list(self._note_sel)))
                m.popup(event.x_root, event.y_root)
                return
            m.add("还原此项", icon="move",
                  command=lambda: self.recycle_restore(rid=nid))
            rec = store().recycle_get(nid)
            is_folder = (rec or {}).get("kind") == "folder"
            m.add("清空当前文件夹" if is_folder else "清空当前笔记",
                  icon="trash", danger=True,
                  command=lambda: self.recycle_purge_item(nid))
            m.popup(event.x_root, event.y_root)
            return
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            return
        # 右键时若该项不在当前多选集合中，则以它为唯一选中
        if nid not in self._note_sel:
            self._note_sel = {nid}
            self._note_anchor = nid
            self._restyle_notes()
        m = PopupMenu(self.root, self.pal)
        if len(self._note_sel) > 1:
            # 全是示例笔记（实际只有一篇）时整项置灰；混选时照常可点，
            # 由 _delete_notes 把示例笔记剔除后再删其余的。
            picked = [x for x in self.data["notes"] if x["id"] in self._note_sel]
            only_sample = bool(picked) and all(self.is_protected_note(x) for x in picked)
            m.add("删除选中的 %d 条笔记" % len(self._note_sel), icon="trash",
                  danger=True, enabled=not only_sample,
                  command=lambda: self._delete_notes(list(self._note_sel)))
            m.popup(event.x_root, event.y_root)
            return
        protected = self.is_protected_note(n)
        who = "自动汇总表" if self.is_alarm_index(n) else "内置说明"
        m.add("置顶" if not n["pinned"] else "取消置顶", icon="pin",
              enabled=not protected, command=lambda: self._popup_pin(nid))
        m.add("星标" if not n.get("starred", False) else "取消星标", icon="star",
              enabled=not protected, command=lambda: self.toggle_star(nid))
        goto = self._goto_item(n)
        if goto:
            m.add(goto[0], icon="folder", enabled=goto[1] and not protected,
                  command=lambda: self._popup_goto_folder(nid))
        # 「移动到文件夹…」：拖拽只认左栏**此刻看得见**的那一行，收着的子层得先
        # 逐级展开；右键给一条直达路径，从整棵文件夹树里挑（见 _popup_move_note）。
        # 不能移的两种情况照系统文件夹菜单的规矩办：项留着、置灰、写明原因，
        # 凭空少一行会让人以为「是不是没点到」。
        targets = ([] if protected else
                   self._note_move_targets(exclude=[n.get("folder")]))
        if protected:
            move_label = "移动到文件夹…（%s不可移）" % who
        elif not targets:
            move_label = "移动到文件夹…（没有其他文件夹）"
        else:
            move_label = "移动到文件夹…"
        m.add(move_label, icon="move", enabled=bool(targets),
              command=lambda: self._popup_move_note(nid))
        if not protected:
            sealed = secret.is_blob(n.get("sec"))
            has_rc = secret.has_recovery(n.get("sec"))
            if sealed:
                unlocked = bool(self._note_key(nid) and n.get("content"))
                if unlocked:
                    m.add("修改加密口令…", icon="lock",
                          command=lambda: self.change_note_password(nid))
                    m.add("取消加密…", icon="unlock",
                          command=lambda: self.clear_note_password(nid))
                    m.add("重新生成本篇恢复码…" if has_rc else "为本篇生成恢复码…",
                          icon="lock", command=lambda: self.add_recovery_code(nid))
                else:
                    m.add("输入口令解锁…", icon="lock",
                          command=lambda: self.unlock_note(nid))
                    if has_rc:
                        m.add("用恢复码恢复…", icon="lock",
                              command=lambda: self.recover_note_with_code(n))
            else:
                m.add("设置加密口令…", icon="lock",
                      command=lambda: self.set_note_password(nid))
        m.add_separator()
        m.add(("删除笔记（%s，不可删）" % (
                  "自动汇总表" if self.is_alarm_index(n) else "内置示例"))
              if protected else "删除笔记",
              icon="trash", danger=True, enabled=not protected,
              command=lambda: self._popup_delete(nid))
        m.popup(event.x_root, event.y_root)

    def _popup_pin(self, nid):
        self.data["current_note"] = nid
        self.toggle_pin()

    def _popup_delete(self, nid, forever=False):
        # ⚠️ 只把 nid 传下去，**不写** current_note：右键点的那一行未必是编辑器里
        # 正打开的那篇，写进去等于「删一篇、顺手把另一篇也关了」。
        self.delete_note(nid, forever=forever)

    # ---------- 跳到所属文件夹（智能分类 / 多级文件夹的父视图） ----------

    def _goto_item(self, n):
        """「跳到所属文件夹」这一行该不该出现，返回 (文案, 是否可用) 或 None。

        两种视图里笔记都不在「眼前这个文件夹」里：

        - 智能分类（全部 / 星标 / 加密）：跨文件夹的聚合视图，压根看不出笔记住在哪；
        - 多级文件夹的**父视图**：父级计数与列表都含整棵子树，翻到的笔记可能住在
          某个子文件夹里，想接着在原处干活同样得先找过去。

        笔记就摆在当前文件夹时不给这一行 —— 原地不动，多一行反而费解。
        智能分类里 folder 指向已删除文件夹时仍然给这一行，但置灰（让用户知道有这回事，
        而不是菜单里凭空少一行）；普通文件夹视图不会出现这种笔记，无须兜。
        """
        cur = self.data.get("current_folder")
        home = self._note_home_folder(n)
        if cur in SMART_FOLDERS:
            return ("跳到所属文件夹", home is not None)
        if home is not None and home["id"] != cur:
            return ("跳到所属文件夹", True)
        return None

    def _note_home_folder(self, n):
        """笔记真正落脚的那个文件夹；指向智能分类或已删除的文件夹时返回 None。"""
        fid = (n or {}).get("folder")
        if not fid or fid in SMART_FOLDERS:
            return None
        return next((f for f in self.data["folders"]
                     if f["id"] == fid and not f.get("smart")), None)

    def _popup_goto_folder(self, nid):
        """「跳到所属文件夹」：切到这条笔记所在的文件夹，把它选中并滚到眼前。

        入口有两处：智能分类（跨文件夹的聚合视图）与多级文件夹的父视图（含子树）。
        两处都看不出笔记具体住在哪，想接着在原文件夹里干活时就得先找回去。
        这里一次做完三件事：逐级展开路径上收着的各级（否则目标行根本没渲染出来）、
        切视图、定位笔记。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        home = self._note_home_folder(n)
        if home is None:
            self._show_info("提示", "这条笔记所属的文件夹已不存在。")
            return
        fid = home["id"]
        # 1) 祖先自上而下逐级展开：顺序反了的话，展开下一级时它自己还没被渲染出来。
        #    就地插行而不重建整列 —— 整列重建会让别的文件夹跟着跳，这次跳转本来
        #    就是为了「稳住视线」。
        expanded = False
        for anc in folder_ancestors(fid, self.data["folders"]):
            if anc.get("collapsed"):
                anc.pop("collapsed", None)
                expanded = True
                self._insert_folder_rows(anc["id"])
        if expanded:
            save_data(self.data)
        # 2) 切视图。这里不复用 select_folder()：它会先把 current_note 清成 None
        #    再起一次编辑器，正文区白闪一下；直接把会话状态摆到位只刷一次列表。
        self.data["current_folder"] = fid
        self.data["current_note"] = nid
        self._note_sel = {nid}
        self._note_anchor = nid
        self._sync_folder_sel_to_note(nid)     # 左栏高亮跟到目标文件夹（内含 _restyle_folders）
        self.refresh_notes()
        # 3) 定位：两边都可能落在视口外
        self._scroll_note_into_view(nid)
        self._scroll_folder_into_view(fid)
        self.load_editor()

    def _goto_note(self, nid):
        """跳到某条笔记（`@` 关联、内链点击的落点）。

        目标不在当前文件夹时复用 `_popup_goto_folder`：逐级展开、切视图、两侧定位
        都是现成的；就在眼前时直接选中，免得整列重建让列表闪一下。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if n is None:
            self._show_info("提示", "这条笔记已经不存在了。")
            return
        if self.data.get("current_note") == nid:
            return                                   # 已经在这一篇，不动
        self._mention_close()
        home = self._note_home_folder(n)
        if home is None:
            self._show_info("提示", "这条笔记所属的文件夹已不存在。")
            return
        if nid in self._rows and self.data.get("current_folder") == home["id"]:
            self.select_note(nid)
            self._scroll_note_into_view(nid)
            return
        self._popup_goto_folder(nid)

    # ---------- @ 关联笔记 ----------

    def _mention_after_space(self, event=None):
        """<space> 的另一路监听：可能是「@ + 空格」。同样**不能** return "break"。

        与 markdown 前缀转换共用 `<space>`：那条走行首，这条看光标前有没有 `@`。
        """
        if not self.current_note() or self.is_readonly():
            return None
        if self._col_active():      # 列模式：空格是「每行插一个」，不是 `@` 触发
            return None
        try:
            ln = self.text.index("insert").split(".")[0]
        except Exception:
            return None
        self._after_idle(lambda: self._mention_maybe_open(ln))
        return None

    def _mention_maybe_open(self, ln=None):
        """触发判定 + 开面板 / 刷新候选。

        只在「查询词还是空的」那一刻开面板：用户敲 `@ ` 就算触发，之后继续打字
        只是过滤（不然每敲一个字都会重新弹一次）。
        """
        pop = getattr(self, "_mention_pop", None)
        hit = self._mention_at(ln)
        if hit is None:
            if pop is not None and pop.is_open():
                self._mention_close()
            return
        if pop is None or not pop.is_open():
            if hit[1]:                       # 已经带查询词了：说明不是刚敲的空格
                return
            # 弹框自带搜索框：键盘输入走 on_query 重算候选；关面板走 on_close
            # 统一收口；关闭时把焦点还给正文，避免打字落进已销毁的输入框。
            pop = MentionPopup(self.root, self.pal,
                               on_pick=self._mention_pick,
                               on_query=self._mention_apply_query,
                               on_close=self._mention_close,
                               focus_back=self.text)
            self._mention_pop = pop
            self._mention_start = hit[0]
            x, y = self._mention_anchor_xy()
            pop.open(self._mention_candidates(""), x, y)
            return
        self._mention_start = hit[0]
        if not pop.search_focused():
            pop.set_query(hit[1])

    def _mention_apply_query(self, query):
        """搜索框里（或正文里同步过来的）查询词变了：重算候选。"""
        pop = getattr(self, "_mention_pop", None)
        if pop is None or not pop.is_open():
            return
        pop.set_items(self._mention_candidates(query))

    def _mention_recheck(self, event=None):
        """光标动了（方向键 / 滚轮）：推迟到 idle 再核对 —— 实例绑定先于类绑定，
        现在核对的话光标还没被 Home / End 挪走，核对等于没核。"""
        if getattr(self, "_mention_pop", None) is None:
            return None
        self._after_idle(self._mention_refilter)
        return None

    def _mention_refilter(self):
        """正文又变了一个字：重算候选（走 <<Modified>>，中文输入也能跟得上）。

        ⚠️ 焦点在弹框搜索框里时，正文里的 `@ ` 不会变（用户敲的字都进了搜索框），
        此时拿正文的空查询词去同步会**盖掉**用户已经输入的搜索词。只更新插入起点，
        查询词交给搜索框自己的 on_query。焦点没抢到（仍在正文）时走老路：把正文
        里的查询词同步进搜索框，顺带触发同一份重算。
        """
        pop = getattr(self, "_mention_pop", None)
        if pop is None or not pop.is_open():
            return
        hit = self._mention_at()
        if hit is None:
            self._mention_close()
            return
        self._mention_start = hit[0]
        if not pop.search_focused():
            pop.set_query(hit[1])

    def _mention_at(self, ln=None):
        """光标前是不是一个待补全的 `@查询`；是就返回 (起点 index, 查询词)。"""
        try:
            idx = str(self.text.index("insert"))
            ln_s, col_s = idx.split(".")
            if ln is not None and str(ln) != ln_s:
                return None
            line = self.text.get(ln_s + ".0", ln_s + ".end")
        except Exception:
            return None
        found = find_mention(line, int(col_s))
        if not found:
            return None
        return "%s.%d" % (ln_s, found[0]), found[1]

    def _mention_anchor_xy(self):
        """浮层锚点：光标的屏幕坐标（下方 4px）；量不到光标就退到窗口左上。"""
        try:
            b = self.text.bbox("insert")
        except Exception:
            b = None
        if b:
            return (self.text.winfo_rootx() + b[0],
                    self.text.winfo_rooty() + b[1] + b[3] + 4)
        return (self.root.winfo_rootx() + 80, self.root.winfo_rooty() + 180)

    def _mention_candidates(self, query):
        """候选笔记：排除自己、排除还锁着的（标题是「首字***」，选了也没法看）。"""
        cur = self.current_note()
        cid = cur["id"] if cur else None
        out = []
        for n in self.data["notes"]:
            if n["id"] == cid:
                continue
            if self._is_sealed(n):
                continue
            out.append({"id": n["id"], "title": self.title_of(n),
                        "sub": self._folder_path_of(n),
                        "updated": n.get("updated") or 0})
        return rank(out, query)

    def _folder_path_of(self, n):
        """笔记所在文件夹的完整路径（工作 / 子目录），同名笔记靠它分辨。"""
        fid = (n or {}).get("folder")
        if not fid:
            return ""
        names = [f["name"] for f in folder_ancestors(fid, self.data["folders"])
                 if f.get("name")]
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if f and f.get("name"):
            names.append(f["name"])
        return " / ".join(names)

    def _mention_pick(self, item):
        """选中一条：把 `@查询` 整段换成 `@标题` 并套上内链。

        ⚠️ 起点必须**先**取再关面板：`_mention_close()` 会把 `_mention_start` 清成
        None，顺序反了就只能拿光标当起点 —— 于是 `@查询` 那段留在正文里，
        链接插在它后面（看起来就是「@ 会议@会议记录」）。
        """
        if not item:
            self._mention_close()
            return
        start = getattr(self, "_mention_start", None)
        nid = item.get("id")
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if n is None or not self.current_note():
            self._mention_close()
            return
        if self._refuse_readonly():
            self._mention_close()
            return
        self._mention_close()
        self.text.config(state=tk.NORMAL)
        try:
            start = start or self.text.index("insert")   # 兜底：起点丢了就用光标
            end = self.text.index("insert")
            if self.text.compare(start, ">=", end):
                end = self.text.index(start + " +1c")
            label = mention_text(self.title_of(n))
            self.text.edit_separator()
            self.text.delete(start, end)
            self.text.insert(start, label)
            end2 = self.text.index("%s +%dc" % (start, len(label)))
            self._apply_link(start, end2, note_url(nid))
            self.text.mark_set("insert", end2)
            self.text.see("insert")
        finally:
            self.text.edit_separator()
        # 焦点可能刚从弹框的搜索框还回来，也可能一直在正文；显式落一次，
        # 保证插入完接着打字是打在 `@标题` 后面，而不是留在已销毁的输入框上。
        self.text.focus_set()
        self.mark_modified()

    def _mention_key(self, event):
        """面板开着时把 ↑ ↓ Esc 让给它；没开就照常（不影响光标移动）。"""
        pop = getattr(self, "_mention_pop", None)
        if pop is None or not pop.is_open():
            return None
        ks = getattr(event, "keysym", "")
        if ks == "Up":
            return "break" if pop.move(-1) else None
        if ks == "Down":
            return "break" if pop.move(1) else None
        if ks == "Escape":
            self._mention_close()
            return "break"
        return None

    def _mention_accept(self):
        """回车 / Tab：吃下当前候选；没得选（空列表）就收起面板、回车照常换行。"""
        pop = getattr(self, "_mention_pop", None)
        if pop is None or not pop.is_open():
            return False
        ok = pop.accept() is not None
        if not ok:
            self._mention_close()
        return ok

    def _mention_close(self):
        pop = getattr(self, "_mention_pop", None)
        if pop is not None:
            pop.close()
        self._mention_start = None

    # ---------- 涂鸦标注（临时模态，关闭即丢弃） ----------

    def doodle_active(self):
        """涂鸦蒙层是否开着。所有会换掉正文的入口都拿它守卫。"""
        d = getattr(self, "_doodle", None)
        return bool(d is not None and d.active)

    def toggle_doodle(self):
        """⋯ 菜单 / Ctrl+Shift+D：进入或退出涂鸦标注。

        涂鸦不改笔记内容（笔迹在独立窗口里，退出即 destroy），所以只读笔记、
        甚至没选中笔记（空编辑区）都能进；退出一律按进入时的快照还原。
        """
        d = getattr(self, "_doodle", None)
        if d is not None and d.active:
            d.close()
            return
        d = DoodleLayer(self, self.pal)
        try:
            ok = d.open()
        except Exception:
            # 建窗失败：open() 把「锁正文」放在最后一步，这里只需把可能建出来的
            # 半截窗口收掉，不会出现一个被 DISABLED 锁死的编辑器。
            try:
                d.close()
            except Exception:
                pass
            return
        if ok:
            self._doodle = d

    # ---------- 只读：内置示例说明笔记 ----------
    # 放行的按键：导航、选择、复制 / 全选，以及全局快捷键。
    # 注意 widget 级绑定返回 "break" 会把 bind_all 一起掐断，所以 Ctrl+N / Ctrl+S /
    # Ctrl+, 必须列进放行名单——否则打开只读笔记时连新建和设置都用不了。
    _RO_PASS_KEYS = frozenset((
        "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Escape", "Insert", "Caps_Lock", "Num_Lock", "Menu", "Print",
    ))
    # "f" 已从名单里撤掉：Ctrl+F 聚焦搜索的绑定按需求取消了，放行反而会让
    # Tk 自带的 emacs「光标右移」在只读笔记里生效、别处不生效，两处不一致。
    _RO_PASS_CTRL = frozenset(("c", "a", "s", "n", "comma"))

    def is_readonly(self):
        """当前笔记是否只读（内置示例说明）。"""
        return bool(getattr(self, "_readonly", False))

    def _refuse_readonly(self):
        """修改类操作的统一守卫。只读时拦下并闪一下提示条，返回 True 表示已拦。"""
        if not self.is_readonly():
            return False
        self._flash_readonly_hint()
        return True

    def _readonly_key_guard(self, event):
        """拦住会改内容的按键；导航 / 选择 / 复制 / 全局快捷键照常放行。"""
        if not self.is_readonly():
            return None
        ks = event.keysym
        if ks in self._RO_PASS_KEYS:
            return None
        if len(ks) > 1 and ks[0] == "F" and ks[1:].isdigit():
            return None                     # 功能键不产生输入
        if event.state & 0x0004 and ks.lower() in self._RO_PASS_CTRL:
            return None
        self._flash_readonly_hint()
        return "break"

    def _readonly_clip_guard(self, event=None):
        """拦住粘贴 / 剪切 / 清空 / 撤销 / 重做这些虚拟事件（代码块内部也用它）。"""
        if not self.is_readonly():
            return None
        self._flash_readonly_hint()
        return "break"

    def _redo_shortcut(self):
        """重做。只读笔记里不允许——撤销栈一动，内容就变了。

        Ctrl+Y 已让给「删除整行」，这里改由 Ctrl+Shift+Z 触发。
        """
        if self._refuse_readonly():
            return
        try:
            self.text.edit_redo()
        except Exception:
            # 没有可重做的操作（撤销栈空，或刚才那次编辑已经把重做栈清空）
            pass

    def _restore_readonly(self):
        """把只读笔记的正文按内存里的原文重载一遍（回滚绕过守卫的改动）。

        递归保护：load_editor 里的 delete/insert 本身也会触发 <<Modified>>，
        那时这个标志还是 True，on_edit 直接退出，不会自己叫自己。
        """
        if getattr(self, "_ro_restoring", False):
            return
        self._ro_restoring = True
        try:
            self._flash_readonly_hint()
            self.load_editor()
        except Exception:
            pass
        finally:
            self._ro_restoring = False

    def on_edit(self, ev=None):
        if getattr(self, "_loading", False):
            # 载入笔记时是程序自己在改编辑器：先 delete 旧正文、再渲染新的。
            # 这段产生的 <<Modified>> 不是用户编辑 —— 认下来会把「刚清空还没渲染」
            # 的空编辑区当成新正文写回笔记（列表标题跳成「新建笔记」、还会被存库）。
            self.text.edit_modified(False)
            return
        if getattr(self, "_ro_restoring", False):
            return
        if not self.text.edit_modified():
            return
        if self.is_readonly():
            # 只读的最后一道防线。按键守卫挡不住输入法（IME 走的是另一条消息通道）、
            # 也挡不住拖放和多点触控粘贴，这些都会让 <<Modified>> 真的亮起来。
            # 既然内容已经变了，就直接按内存里的原文重载一遍 = 回滚这次改动。
            self.text.edit_modified(False)
            self._restore_readonly()
            return
        if self.text.edit_modified():
            # 先补「刚转成空标题行」的样式，再补整行，保证续写出来的标签被正确保存
            self._apply_pending_line_tag()
            self._sync_line_tags()
            self.mark_modified()
            self.text.edit_modified(False)
            # 折叠体检：光标落进隐藏区→先展开；被删空的折叠清理；chip 挪回头行末。
            # 放在 mark_modified 之后：护栏自己展开折叠不写库，不抢保存链路。
            self._fold_sync_after_edit()
            # 行号 / 查找高亮都依赖「正文现在的样子」：行数变了要重画，命中位置
            # 变了要重找。两个方法自己会判开关，没开行号 / 没开查找框时是空操作。
            self._gutter_schedule()
            self._find_on_edit()
            # `@ 关联笔记`：候选面板开着时，每敲一个字都要重排（中文走输入法也
            # 一样会点亮 <<Modified>>，所以挂在 on_edit 上而不是键盘事件上）
            if getattr(self, "_mention_pop", None) is not None:
                self._mention_refilter()

    def _line_has_tag(self, tag, ln):
        """该行是否带某个行级标签。

        **不能用 `tag_nextrange(tag, ln.0, ln.end)`**：它找的是「范围的**起点**
        落在这个区间内」，上一行连过来的标签起点不在本行，就会被判成「没有」。
        连续多行引用时中间那几行正好命中这个坑（取消引用点了没反应、回车断开
        也断不掉）。改看行首那个字符身上挂了什么标签，与范围从哪儿开始无关。
        """
        try:
            return tag in self.text.tag_names(ln + ".0")
        except Exception:
            return False

    def _tag_covers_line(self, tag, ls, le):
        """标签是否连续覆盖 [ls, le) 整段（无缺口）。"""
        pos = ls
        while self.text.compare(pos, "<", le):
            rng = self.text.tag_nextrange(tag, pos, le)
            if not rng:
                # 区间里找不到起点：可能是「从本行之前就开始」的连续覆盖
                return self._line_has_tag(tag, str(pos).split(".")[0])
            if self.text.compare(rng[0], ">", pos):
                return False                    # 中间有缺口
            pos = rng[1]
        return True

    def _sync_line_tags(self):
        """把光标所在行「已有的行级样式」补满整行，解决续写掉样式的问题。

        Tk 只在**行中间**插入时继承复选框/标签；在**行尾或行首**插入的新字符
        不带任何标签（行尾的前一个字符是换行符，而换行符刻意不带 title，
        免得 title 的 spacing 在标题与正文之间插出空行）。
        所以「在标题后面继续打字」会掉成正文样式。这里每次编辑后把该行已存在的
        行级标签补齐到整行 —— 标题行续写仍是标题，正文行续写仍是正文。

        用户手动把某行设成别的行级样式（如标题行点「正文」）时，该行只有那一个
        行级标签，补齐的也是它自己，不会把用户的显式选择覆盖掉。
        回车换行由 _title_line_break 显式切正文，走的是新行，不受影响。
        """
        if not self.current_note():
            return
        ln = self.text.index("insert").split(".")[0]
        ls, le = ln + ".0", ln + ".end"
        if ln == "1":
            # 第 1 行恒为标题：在首行输入的任何内容都保持 title 样式
            self._ensure_title_line()
            return
        for tag in self.LINE_TAGS:
            if tag == "quote":
                continue                        # 引用在下面单独按「块」处理
            if not self._line_has_tag(tag, ln):
                continue                        # 该行没有这个行级样式
            if not self._tag_covers_line(tag, ls, le):
                self.text.tag_add(tag, ls, le)
            break                               # 只按该行已有的那一种补齐
        # 引用是**块级**语义：一整行要么全在引用里（含换行符），要么完全没有。
        # 最容易被破坏的场景是「删除」：引用行在行首退格并进上一行之后，上一行
        # 的行首仍是 body、行尾那一截却还挂着 quote（底色 + 缩进都还在），
        # 用户看到的就是「删掉引用行、光标回到上一行，结果还是引用状态」。
        # 所以这里不看上面那个循环的结果，一律按**行首**判定、把整行统一：
        #  行首是引用 → 补满整行；行首不是 → 把这一行（含换行符）上的 quote 清光。
        if self._line_has_tag("quote", ln):
            self._sync_quote_line(ls, le)
        else:
            self.text.tag_remove("quote", ls, le + " +1c")
        # 顺手把**上一行**也归一一次：在引用行行尾回车时，Tk 会让插进来的换行符
        # 继承 quote（在标签范围末端插入会继承），那条空行于是又挂上一截色带。
        # 而这里只看光标所在行，光标已经落到新行了 —— 不补这一刀，空引用行
        # 会「删掉又冒出来」，正是要避免的那种残留。
        try:
            prev = str(int(ln) - 1)
        except Exception:
            prev = ""
        if prev and prev != "0":
            pls, ple = prev + ".0", prev + ".end"
            if self.text.compare(pls, "==", ple) \
                    and self._line_has_tag("quote", prev):
                self.text.tag_remove("quote", pls, ple + " +1c")
        # 待办方框的标签归一：手敲 / 粘贴进来的方框没有 todo 标签，
        # 字符本身认得出，就顺手把字号、颜色和点击绑定一起补上。
        self._tag_todo_prefix(ln)

    def _ensure_title_line(self):
        """第 1 行恒为标题样式。

        「笔记第一行默认标题，只要在第一行输入都设为标题样式」——渲染完成后
        和每次编辑后都会调用，把首行的行级样式统一成 title。旧数据里首行可能
        存的是 body / h2 / quote，打开即纠正；空首行只清残留（打字后由
        _sync_line_tags 补上）。标题换行符刻意不带 title（spacing 不插空行）。

        ⚠️ 这里**不做**任何首行专属的间距修正：title 的 spacing1/3(8/8) 必须完整
        生效，选中标题时上下才对称；顶部多出的 8px 已由 TEXT_PADY(12) 让出来。
        旧版在此挂 firstline 把 spacing1 压成 0，已移除（见 widgets.TEXT_PADY）。
        """
        try:
            ls, le = "1.0", "1.end"
            for t in self.LINE_TAGS:
                if t == "title":
                    continue
                # quote 的清法要含换行符（否则残留一条色带），其余行级样式到行尾
                end = (le + " +1c") if t == "quote" else le
                self.text.tag_remove(t, ls, end)
            if not self.text.compare(ls, "==", le):
                self.text.tag_add("title", ls, le)
        except Exception:
            pass

    def _quote_span_end(self, ls, le):
        """从行首起，quote 连续覆盖到哪个下标（遇到缺口就停）。

        **不能用 tag_nextrange**：它按「区间的起点」找，起点在本行之前（上一行
        连过来、或本行前几个字就有）的那种区间永远找不到，结果会把「整行都在
        引用里」误判成「本行没有引用」。改成直接遍历标签区间集合，逐个往后接。
        """
        limit = le + " +1c"
        pos = ls
        try:
            ranges = self.text.tag_ranges("quote")
        except Exception:
            return pos
        for i in range(0, len(ranges), 2):
            a, b = ranges[i], ranges[i + 1]
            if self.text.compare(b, "<", pos):
                continue                    # 这个区间完全在 pos 之前
            if self.text.compare(a, ">", pos):
                break                       # 中间有缺口：连续性到此为止
            if self.text.compare(b, ">", pos):
                pos = b
                if self.text.compare(pos, ">=", limit):
                    break
        return pos

    def _sync_quote_line(self, ls, le):
        """引用行在编辑后的补/剪。

        引用是**行级**语义，但 Tk 的 tag 是字符级的，删除/合并之后会留下两种
        残留：删光引用行文字后换行符还挂着 quote（空行色带）；正文行行首退格
        并入引用行后行首仍带 quote（染到正文）。处理原则：
          空行 → 清掉（「引用删掉后引用色不必保留」）；
          铺满 → 不动；只差换行符 → 补上；半行 → 剪掉尾巴（正文不被误染）。

        空行这条**改过一次**：早先刻意补满（"没有字也要显示，除非删除"），
        代价是把引用文字删光之后仍挂着一条灰底空行，看着像删不掉的残留 ——
        用户明确要求不必保留。现在删光即退出引用，空引用行不再存在。
        """
        if self.text.compare(ls, "==", le):
            # 空行不保留引用色块：范围的起点就是换行符，摘掉它整行就没有底色了
            self.text.tag_remove("quote", ls, le + " +1c")
            return
        end = self._quote_span_end(ls, le)
        if self.text.compare(end, ">=", le + " +1c"):
            return                                  # 已铺满整行（含换行符）
        if self.text.compare(end, ">=", le):
            self.text.tag_add("quote", end, le + " +1c")   # 只差换行符 → 补上
        else:
            self.text.tag_remove("quote", end, le + " +1c")  # 半行 → 剪掉尾巴

    def mark_modified(self):
        if self.is_readonly():
            return              # 双保险：只读笔记的任何"已修改"都不该落到数据里
        n = self.current_note()
        if not n:
            return
        # 内存立即更新（保证列表标题/预览、切换笔记、导出等都读到最新内容）
        n["content"] = self.serialize()
        # 链接文字被删光后，links 里那条 URL 就成了孤儿：顺手清掉，
        # 否则以后新建链接拿到同一个 id 时，旧地址会"复活"。
        self._prune_links(n)
        # 加密笔记：内容一变就立刻重新加密（用的是解锁时缓存的密钥，不跑 PBKDF2）。
        # 必须在落库前做——数据库里永远只有密文，没有"忘了加密"的时间窗。
        if secret.is_blob(n.get("sec")) and self._note_key(n["id"]):
            self._seal_into(n)
        n["updated"] = __import__("time").time() * 1000
        self.text.edit_modified(False)
        # 落库与列表行刷新去抖合并：输入连贯时不打断输入，停顿后再做重活
        self._schedule_save()
        self._schedule_active_row()

    def _schedule_save(self):
        """把「写数据库」推迟到输入停顿之后；连续输入只落一次。"""
        if self._save_job is not None:
            self._cancel_after(self._save_job)
        self._save_job = self._after(SAVE_DEBOUNCE_MS, self._flush_save)

    def _flush_save(self):
        """立刻把内存数据落库（切换笔记 / 搜索 / 退出前调用，确保不丢、不过期）。"""
        if self._save_job is not None:
            self._cancel_after(self._save_job)
            self._save_job = None
        if save_data(self.data) is False:
            # 保存失败就别动文件：数据在内存里，图也得留着做兜底快照/重试的料
            return
        # 落库成功后再回收孤儿图片：正文里被删掉的那张图，serialize 已经不再
        # 引用它，这里把它在 data/media/ 下的文件一起删掉。放在 debounce 落库
        # 之后而不是按键当场 —— 给「删了立刻撤销」留出把引用加回来的窗口。
        self._img_gc(resave=False)

    def _schedule_active_row(self):
        """列表行的标题/预览刷新也去抖：不与输入抢主线程，但依然跟手。"""
        if self._row_job is not None:
            self._cancel_after(self._row_job)
        self._row_job = self._after(ROW_REFRESH_DEBOUNCE_MS, self._flush_active_row)

    # 悬挂缩进的动态标签前缀：每个列表行一个（li_<行号>），见 _sync_list_indent
    LI_TAG_PREFIX = "li_"

    def _list_text_start(self, ln):
        """该行正文首字的下标（跳过列表标记 / 待办勾选框），不是列表行则返回 None。

        待办勾选框现在是字面字符（⬜ / ✅，见 md_conv.TODO_OFF），和项目符号
        走同一套前缀识别 —— 不再需要先认嵌入式窗口。
        """
        try:
            txt = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return None
        kind, prefix = split_list_prefix(txt)
        if kind is None:
            return None
        return len(prefix)

    def _line_font_measure(self, ln, text, body=False, tag=None):
        """用该行实际生效的字体量出 text 的像素宽度。

        之所以要按「该行」取字体：首行恒为标题，同样的两个全角空格在标题字号下
        比正文宽，按统一字号算出来的悬挂缩进会偏小，续行对不齐。

        `tag` 显式指定取哪个标签的字体 —— 待办的方框吃 todo 标签的图标字体，
        而它前面那两格缩进没有标签、吃正文字体，两者字号不同，必须分开量。
        `body=True` 直接按正文默认字体量（用于缩进与间隙那两段）。
        """
        spec = None
        if tag:
            try:
                spec = self.text.tag_cget(tag, "font")
            except Exception:
                spec = None
        if not spec and not body:
            for tg in ("todo", "todo_done", "title", "h2", "icode",
                       "bold_italic", "bold", "italic", "body"):
                if not self._line_has_tag(tg, ln):
                    continue
                try:
                    spec = self.text.tag_cget(tg, "font")
                except Exception:
                    spec = None
                if spec:
                    break
        if not spec:
            spec = self.text.cget("font")
        try:
            f = tkfont.Font(font=spec)
        except Exception:
            f = tkfont.Font(family=FONT[0], size=12)
        return f.measure(text)

    def _sync_list_indent(self):
        """列表行 / 待办行的**悬挂缩进**：自动折行后，续行与正文首字对齐。

        列表项是「**字面**标记 + 文字」（标记存在正文里，理由见 md_conv.LI_INDENT：
        缩进跟着标记走，编辑、回车续接、整行删除都不会漂）。代价是 Tk wrap 出来的
        续行一律退回行首 —— 标记凸在左边，文字对不齐。这里给每行挂一个动态标签，
        配 lmargin2 = 该行标记（含缩进）的像素宽度，续行就从文字起点起排。

        三个 Tk 细节，每条都是实测踩出来的：
        - **宽度一律用字体测量，不用 `count -xpixels`**。后者实测不可靠：同一个
          控件里对第 1 行能返回 (42,)、对第 2 行直接返回 None（与索引是否合法无关），
          也没有报错。bbox 差值倒是准，但**只对当前可见的行有效** —— 长文档里
          滚出视口的列表行会量不到。字体测量不依赖布局，也不受可见性影响。
        - **必须每行一个独立标签实例**。Tk 按「标签范围的第一段」算 lmargin1、
          其余各段算 lmargin2；多行共享一个连续范围时只有第一行拿到 lmargin1，
          后面每行都要在 lmargin2 之上再叠自己的字面标记 → 越往后越往右错。
        - **范围不能含换行符**（到 `.end` 为止）。相邻两行首尾相接时 Tk 视为同一个
          段落，效果同上。（这一点正好和 quote 相反 —— quote 要铺满背景，非含
          换行符不可。）lmargin 不影响底色的铺法，不用担心背景。

        全量重算而非增量维护：行号一变（中间插删行）增量表就全错，而 measure 很便宜、
        行数又在几百量级，重建比记账便宜也更不容易错。
        """
        t = self.text
        try:
            for tag in list(t.tag_names()):
                if tag.startswith(self.LI_TAG_PREFIX):
                    t.tag_delete(tag)               # 连配置一起删，避免残留旧宽度
            last = int(t.index("end-1c").split(".")[0])
        except Exception:
            return
        for ln in range(1, last + 1):
            s = str(ln)
            idx = self._list_text_start(s)
            if not idx:
                continue
            try:
                txt = t.get(s + ".0", s + ".end")
                # 标记宽度一律按「该行字体下这段前缀的实际像素宽度」量：
                # 待办的方框是放大四号的字符，跟项目符号不是同一个字号。
                kind, prefix = split_list_prefix(txt)
                if kind == "check":
                    # 待办前缀掺了**两种字号**：缩进与间隙吃正文字体，方框吃
                    # todo 标签的图标字体（大一号）。整段按一种字体量差出 6px，
                    # 续行就对不齐 —— 所以逐段量（实测 32 + 18 + 10 = 60）。
                    off = len(prefix) - len(prefix.lstrip("\u3000 "))
                    w = (self._line_font_measure(s, prefix[:off], body=True)
                         + self._line_font_measure(s, prefix[off:off + 1], tag="todo")
                         + self._line_font_measure(s, prefix[off + 1:], body=True))
                else:
                    w = self._line_font_measure(s, prefix)
            except Exception:
                continue
            if not w or w <= 0:
                continue
            tag = self.LI_TAG_PREFIX + s
            try:
                t.tag_add(tag, s + ".0", s + ".end")
                t.tag_configure(tag, lmargin1=0, lmargin2=int(w))
            except Exception:
                pass

    def _upgrade_list_marks(self):
        """把正文里**老形态**的列表标记换成现行形态（每篇载入时跑一次）。

        老形态是「符号 + 半角空格」。半角空格是 Tk 的换行断点，而跟在它后面的
        那串没有空格的汉字是一个不可断的词 —— 两者一碰就是实打实的排版 bug：
        列表文字一长，第一行只剩符号，正文全体掉到第二行。NBSP 不可断，换掉
        尾部那两个字符就焊住了。

        换的是**标记本身**而不是用户写的文字，而且是等宽等长替换：显示看不出
        变化，行内下标、标签范围、全文长度一概不动。

        只改组件里的文本，不动 note["content"] —— 用户真正编辑这篇时才会连标记
        一起写回库里；没碰过的笔记保持原样。必须赶在 edit_reset() 前跑，
        免得这一步进撤销栈。
        """
        t = self.text
        try:
            last = int(t.index("end-1c").split(".")[0])
        except Exception:
            return
        for ln in range(1, last + 1):
            s = str(ln)
            try:
                txt = t.get(s + ".0", s + ".end")
            except Exception:
                continue
            kind, prefix = split_list_prefix(txt)
            if kind == "check":
                # 待办行：间隙归位（老数据里可能是一个半角空格），方框本身的
                # 旧字形也一并换掉（☐ / ⚪ / ▢ → ⬜，☑ → ✅），全部一换一。
                self._upgrade_check_space(s, txt)
                i = len(txt) - len(txt.lstrip("\u3000 "))
                # 贴行首的老待办补两格缩进，与项目符号 / 三角 / 序号对齐
                # （那时还没有这条规矩）。**这一步会插入字符**——不同于上面那些
                # 等长替换，但它跑在 edit_reset() 之前，不进撤销栈，下面的列号
                # 也全部按新 txt 重算，安全。
                if i == 0:
                    t.insert(s + ".0", LI_INDENT, ())
                    txt = LI_INDENT + txt
                    i = len(LI_INDENT)
                new = TODO_LEGACY.get(txt[i:i + 1])
                if new:
                    self._swap_mark_chars(s, txt[i:], new + txt[i + 1:], col0=i)
                continue
            if kind is None:
                continue
            if txt[:len(prefix)] == prefix:
                continue
            self._swap_mark_chars(s, txt, prefix)

    def _swap_mark_chars(self, s, old, new, col0=0):
        """逐字符替换行首标记，每个字符**带走它原有的标签**。

        整段 delete + insert 图省事，但那会把这一段上的行级标签（标题 / 加粗）
        一起抹掉，第一个字就掉回正文字号。`col0` 是这段在本行里的起始列
        （待办行前面可能还有两格缩进）。
        """
        t = self.text
        for i, ch in enumerate(new):
            if old[i] == ch:
                continue
            idx = "%s.%d" % (s, col0 + i)
            tags = t.tag_names(idx)
            t.delete(idx)
            t.insert(idx, ch, tags)

    def _upgrade_check_space(self, s, txt):
        """老笔记的待办行：勾选框后面那一串普通空格 → 现行的两个 NBSP（TODO_GAP）。

        以前勾选框是嵌入控件，字符流里只有它后面那个空格；现在框本身是字符，
        老数据渲染出来就是「方框」+「  」+ 文字（框后面自带间隙之后还多一个）。
        半角空格是 Tk 的换行断点，不换掉的话待办文字一长照样掉到第二行。
        这里把框后面那**整串空白**统一压成 TODO_GAP —— 与 ● / ➤ / 1. 尾部
        同宽，四种列表「标记 → 正文」的距离才一致。

        列号要按行首缩进算：待办行常常是「两格缩进 + 框」（以前写死第 1 列，
        带缩进的老行根本进不来）。
        """
        start = len(txt) - len(txt.lstrip("\u3000 ")) if txt else 0
        if txt[start:start + 1] not in TODO_MARKS:
            return
        i = start + 1
        while i < len(txt) and txt[i] in " \u3000\u00a0\t":
            i += 1
        if txt[start + 1:i] == TODO_GAP:
            return                       # 已经是现行形态
        t = self.text
        try:
            tags = t.tag_names("%s.%d" % (s, start + 1))
            if i > start + 1:
                t.delete("%s.%d" % (s, start + 1), "%s.%d" % (s, i))
            t.insert("%s.%d" % (s, start + 1), TODO_GAP, tags)
        except Exception:
            pass

    def _flush_active_row(self):
        self._row_job = None
        self._update_active_row()
        self._refresh_footer()
        # 列表缩进随内容变（行号增删、勾选项插删），跟着这次去抖一起重算
        self._sync_list_indent()
        # 在命中词中间接着打字时，新字符会继承 find 背景，一路黄下去。
        # 按当前关键词重标一遍就把「多出来的那截」擦掉了（搜索词为空时是空操作）。
        self._apply_search_highlight()

    def _update_active_row(self):
        """编辑时只就地更新当前笔记那一行的标题/预览/时间，避免整列重建导致闪烁。"""
        n = self.current_note()
        if not n or not self._active_title:
            return
        # 标题是自绘 Canvas（可按像素滚动），不能用 configure(text=...)：
        # 更新完整文字后重新按可用像素截断（编辑标题时列表标题实时跟随）
        rec = self._rows.get(n["id"])
        if rec:
            rec["row"]._full_name = self.title_of(n)
            self._fit_note_title(n["id"])
        # 标题是正文第一行 → 标签条上那一片也要跟着走（只改这一片，不整条重建）
        try:
            self.tabbar.set_title(n["id"], self.title_of(n))
        except Exception:
            pass
        self._active_preview.configure(
            text=self.trunc(self.preview_of(n), 22) or "暂无更多文字")
        self._active_date.configure(text=self._date_text(n))

    def _is_ephemeral_tag(self, tag):
        """运行时派生、不该落库的标签。

        悬挂缩进的 li_<行号> 每次重算都按当前行号整体重建：它派生于内容，
        存进库里只会让 characters 的 tags 越积越多（旧行号永不失效），
        重开时还会带着不存在的配置被渲染出来。

        `find`（搜索命中）与 `locked_ph`（加密占位）同理：前者是「当前搜索」的
        临时叠加，存进库里就是一段永远抹不掉的黄底；后者根本不是正文内容。
        `colsel`（竖向选择的矩形高亮）是最新的一个 —— 它纯粹是「刚才选过哪块」的
        显示状态，落库只会让重开时莫名多出一片蓝底。
        `gfold*`（正文行折叠）同理：折叠是阅读视图状态，随正文存库等于把「当时
        折到哪」也当成了内容；重开回到展开态才是合理默认。
        `firstline` 是旧版（2026-11-05 前）挂在首行压 spacing1 的排版标签，
        现已不再挂载；名字留在过滤表里只为兼容旧库可能存在的残留记录。
        `img_loading`（远程图下载中的占位文字）同理：它是过程态，下载完就消失，
        万一中途落库，颜色/斜体也不该被当成用户设置过的格式。
        """
        tag = str(tag)
        return (tag.startswith(self.LI_TAG_PREFIX)
                or tag.startswith("gfold")
                or tag in ("find", "locked_ph", "colsel", "firstline",
                           IMG_LOADING_TAG))

    def serialize(self):
        # 未解锁的加密笔记：编辑器里躺的是「文字已加密」这句占位话，它不是正文，
        # 被写回去就等于把密文替换成五个字（真内容当场蒸发）。任何入口一律返回空。
        if getattr(self, "_locked_view", False):
            return []
        # 逐字符记录当前标签，确保重开后格式精确还原
        active = set()
        out = []
        for key, value, index in self.text.dump("1.0", tk.END, window=True, tag=True,
                                               text=True, image=True, mark=False):
            if key == "tagon":
                if value not in ("sel", "done") and not self._is_ephemeral_tag(value):
                    active.add(value)
            elif key == "tagoff":
                if value not in ("sel", "done") and not self._is_ephemeral_tag(value):
                    active.discard(value)
            elif key == "text":
                for ch in value:
                    out.append(["t", ch, sorted(active)])
            elif key == "image":
                # 图片。⚠️ 闹钟在 2026-11-18 改成了 AlarmCard 窗口，不再走这里；
                # 老版本库里残留的 alarm 图名认领保留一段，保证升级中不丢内容。
                d = self._alarms_get().get(value)
                if d:
                    out.append(["alarm", {"id": d.get("id"), "text": d.get("text"),
                                          "at": d.get("at")}])
                    continue
                rec = self._imgs_get().get(value)
                if rec and isinstance(rec.get("block"), list):
                    out.append(rec["block"])
            elif key == "window":
                w = self.text.nametowidget(value)
                if isinstance(w, CodeBlock):
                    out.append(["code", w.lang, w.snapshot()])
                    continue
                if isinstance(w, AlarmCard):
                    out.append(["alarm", w.snapshot()])
                    continue
                if isinstance(w, HRule):
                    # 分割线是结构项，不占字符；它的样式完全由主题决定，无需存内容
                    out.append(["hr"])
                    continue
                # 其它嵌入窗口（历史数据里的待办控件）不落库：待办现在是字符，
                # 载入时已经渲染成 ⬜ / ✅，这里跳过就是彻底断掉旧形态
        return out

    def _emph_state(self, a, b):
        """读范围内当前的「加粗 / 斜体」状态（粗斜体标签同时代表两者）。"""
        bold = bool(self.text.tag_nextrange("bold", a, b)) or \
            bool(self.text.tag_nextrange("bold_italic", a, b))
        italic = bool(self.text.tag_nextrange("italic", a, b)) or \
            bool(self.text.tag_nextrange("bold_italic", a, b))
        return bold, italic

    def _apply_emph(self, a, b, bold, italic):
        """把范围统一成「一种」强调标签，从根上避免 Tk 两个 font 标签互相覆盖。"""
        for t in self.EMPH_TAGS:
            self.text.tag_remove(t, a, b)
        if bold and italic:
            self.text.tag_add("bold_italic", a, b)
        elif bold:
            self.text.tag_add("bold", a, b)
        elif italic:
            self.text.tag_add("italic", a, b)

    def toggle_sel_tag(self, tag):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        sel = self.text.tag_ranges("sel")
        if sel:
            a, b = sel[0], sel[1]
            if tag in ("bold", "italic"):
                # 加粗 / 斜体：按「粗+斜」组合态处理，互不覆盖
                has_b, has_i = self._emph_state(a, b)
                nb = (not has_b) if tag == "bold" else has_b
                ni = (not has_i) if tag == "italic" else has_i
                self._apply_emph(a, b, nb, ni)
            elif tag == "underline":
                # 下划线颜色标签 u_* 自带 underline=True，因此「是否有下划线」
                # 要把它们一起算上，否则设过 U 颜色后点 U 关不掉。
                underlined = bool(self.text.tag_nextrange("underline", a, b)) or any(
                    self.text.tag_nextrange(t, a, b)
                    for t in self.text.tag_names() if t.startswith("u_"))
                if underlined:
                    self.text.tag_remove("underline", a, b)
                    for t in list(self.text.tag_names()):
                        if t.startswith("u_"):
                            self.text.tag_remove(t, a, b)
                else:
                    self.text.tag_add("underline", a, b)
            elif self.text.tag_nextrange(tag, a, b):
                self.text.tag_remove(tag, a, b)
            else:
                self.text.tag_add(tag, a, b)
        else:
            # 未选中文字：切换「接下来输入」的强调态，同样支持叠加
            if tag in ("bold", "italic"):
                cur = set(self.text.tag_names("insert"))
                has_b = bool(cur & {"bold", "bold_italic"})
                has_i = bool(cur & {"italic", "bold_italic"})
                nb = (not has_b) if tag == "bold" else has_b
                ni = (not has_i) if tag == "italic" else has_i
                for t in self.EMPH_TAGS:
                    self.text.tag_remove(t, "insert")
                if nb and ni:
                    self.text.tag_add("bold_italic", "insert")
                elif nb:
                    self.text.tag_add("bold", "insert")
                elif ni:
                    self.text.tag_add("italic", "insert")
            else:
                self.text.tag_add(tag, "insert")
        self.mark_modified()

    def _set_line_tag(self, ln, tag):
        """给某一行设行级样式，并处理「空行」这个特例。

        空行上的标签范围是 0 长度，Tk 的继承规则是「插入点落在标签范围中间才继承」，
        0 长度等于起点即终点，紧接着打的第一个字**不会被继承**——实测 `# ` 转成
        空标题行后打「标题」，tag_nextrange 里找不到 title。所以空行要记一笔待办，
        等第一个字进来再补整行（见 _apply_pending_line_tag）。
        """
        # 第 1 行恒为笔记标题：H2 / 引用 / 正文落到第 1 行一律按 title 处理
        # （「只要在第一行输入，都设为标题样式」）。
        if ln == "1" and tag != "title":
            tag = "title"
        ls, le = ln + ".0", ln + ".end"
        # 引用的范围要多含行尾换行符：Tk 只在 tag 覆盖到换行符时才把 background
        # 铺满整行宽度，否则只铺到最后一个字——多行引用会变成一堆长短不齐的色条。
        # 标题刻意相反（换行符不带 title，免得 spacing 在标题后插出空行），所以
        # 只对 quote 用这个范围，其余行级标签维持 ln.end。
        qle = ln + ".end +1c"
        # 清的时候一律清到 qle：取消引用时若不把换行符上的标记一起去掉，
        # 那行会残留一小条底色（换行符本身没宽度，但 Tk 照样铺整行）。
        for t in self.LINE_TAGS:
            self.text.tag_remove(t, ls, qle)
        if tag != "body":
            self.text.tag_add(tag, ls, qle if tag == "quote" else le)
        is_empty = bool(self.text.compare(ls, "==", le))
        self._md_pending = (ln, tag) if (tag != "body" and is_empty) else None

    def _apply_pending_line_tag(self):
        """空行转成标题/引用后，把第一个字符的样式补上（一次性）。

        之后的续写由 _sync_line_tags 维持整行，这里只负责「从 0 长度变成非空」
        的那一步。
        """
        pend = getattr(self, "_md_pending", None)
        if not pend:
            return
        ln, tag = pend
        try:
            last = self.text.index("end-1c").split(".")[0]
            if int(ln) > int(last):
                self._md_pending = None
                return
            if not self.text.get(ln + ".0", ln + ".end"):
                return                  # 还没开始打字，继续等
        except Exception:
            self._md_pending = None
            return
        self._md_pending = None
        ls, le = ln + ".0", ln + ".end"
        for t in self.LINE_TAGS:
            self.text.tag_remove(t, ls, le)
        self.text.tag_add(tag, ls, (le + " +1c") if tag == "quote" else le)

    def apply_line_tag(self, tag):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        line = self.text.index("insert").split(".")[0]
        # 行级样式互斥：标题、正文、引用三者只能有一个（LINE_TAGS 含 quote）
        self._set_line_tag(line, tag)
        self.mark_modified()

    # 第一行恒为笔记标题（`_set_line_tag` 里第 1 行一律按 title 处理）。标题只承载
    # 标题文字：列表标记是「一个符号领一整句」的行级结构，混进标题就成了 H1 底下
    # 挂一串 •，导出时也理不清层级；待办更不用说 —— 「标题是一条待办」没有意义。
    # 于是四个列表按钮 + 四种 markdown 快捷在第一行一律不生效。
    TITLE_LINE = "1"

    def _title_line_blocked(self, ln):
        """第 1 行（标题行）不允许列表类标记。拦下时提示一句，返回 True 表示已拦。"""
        if str(ln) != self.TITLE_LINE:
            return False
        self._show_info("提示", "第一行是笔记标题，不能加列表项。\n列表请从第二行开始。")
        return True

    def apply_list_kind(self, kind):
        """工具栏四个列表按钮的共同入口：切换 / 互转 / 批量应用。

        规则（P0）：
          · 光标所在行（有选区则选区覆盖的每一行）都已经是目标类型 → **整块取消**
            列表标记（再点一次就是关掉，和 B/I/U 的切换语义一致）；
          · 否则统一转成目标类型；已经是目标类型的行原样不动；
          · 第 1 行是标题行，永远跳过；选区从标题行开始时只处理后面的行。

        多行一起处理而不是逐行「点一次加一个标记」：用户选中五行再点「有序列表」，
        想要的是五行都变成列表，而不是五行各自叠一个 1.。
        """
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        if kind is not None and kind not in LIST_KINDS:
            return
        lines = [ln for ln in self._touched_lines()
                 if str(ln) != self.TITLE_LINE]
        if not lines:
            self._title_line_blocked(self.TITLE_LINE)
            return
        kinds = [self._list_kind_at(ln) for ln in lines]
        target = None if all(k == kind for k in kinds) else kind
        self.text.config(state=tk.NORMAL)
        self.text.edit_separator()
        try:
            for ln in lines:
                self._set_list_kind(ln, target)
            if target == "numbered":
                for ln in lines:
                    self._renumber_numbered_block(ln, sep=False)
        finally:
            self.text.edit_separator()
        self._sync_list_indent()
        self.mark_modified()

    def _list_kind_at(self, ln):
        """该行的列表类型；普通正文返回 None。"""
        try:
            txt = self.text.get("%d.0" % ln, "%d.end" % ln)
        except Exception:
            return None
        # 空项（刚回车出来、还没打字）也算这一类：否则再点一次按钮会叠标记。
        return split_list_prefix(txt, allow_empty=True)[0]

    def _set_list_kind(self, ln, kind):
        """把一行改成 kind（None = 清除标记）。调用方负责撤销边界与收尾。

        缩进跟着行原有缩进走（老行没有缩进时补 LI_INDENT）：同一组列表互转之后
        仍站在同一条竖线上，嵌套层级也不会被「转成有序列表」这一步压平。
        """
        try:
            txt = self.text.get("%d.0" % ln, "%d.end" % ln)
        except Exception:
            return
        old_kind, prefix = split_list_prefix(txt, allow_empty=True)
        if kind == old_kind and kind is not None:
            return
        if kind is None and old_kind is None:
            return
        if old_kind:
            indent = prefix[:len(prefix) - len(prefix.lstrip("\u3000 "))]
            self.text.delete("%d.0" % ln, "%d.0 +%dc" % (ln, len(prefix)))
        else:
            indent = txt[:len(txt) - len(txt.lstrip("\u3000 "))]
        # 从待办转出去时，整行的删除线要一起摘掉；框本身的 todo 标签随字符删除。
        self.text.tag_remove("done", "%d.0" % ln, "%d.end" % ln)
        if kind is None:
            return
        indent = indent or LI_INDENT
        if kind == "check":
            # 方框必须走 _add_checkbutton：todo / todo_done / todo_gap 三个标签
            # 都挂在那里，裸插字符的框点不动（见 _tag_todo_prefix 的说明）。
            self.text.insert("%d.0" % ln, indent, ())
            self._add_checkbutton(checked=False,
                                  index="%d.%d" % (ln, len(indent)))
        else:
            # 显式空标签集：行首插入不指定标签会继承上一行的行级样式（引用 / 标题）。
            self.text.insert("%d.0" % ln, list_marker(kind, indent), ())

    def _numbered_line_info(self, ln):
        """有序列表行 → (前缀, 缩进)；不是有序列表返回 None。"""
        try:
            txt = self.text.get("%d.0" % ln, "%d.end" % ln)
        except Exception:
            return None
        # 回车续接出来的新项还没有正文，但它**在编号序列里**：不认的话整块
        # 重编号会在新项处断开（1,2,2,3 永远修不成 1,2,3,4）。
        kind, prefix = split_list_prefix(txt, allow_empty=True)
        if kind != "numbered":
            return None
        indent = prefix[:len(prefix) - len(prefix.lstrip("\u3000 "))]
        return prefix, indent

    def _renumber_numbered_block(self, ln, sep=True):
        """把 ln 所在的**连续有序列表块**重编成 1..n（P1 自动重编号）。

        「连续」按缩进分组：只给**和 ln 同一缩进**的行编号。比它更深的行
        （子列表 / 缩进续写）跳过但不打断父级编号；遇到空行、同层或更浅的
        正文、别的列表类型才断开。缩进不同视为子列表，不并进同一组编号。

        只在序号真的变化时才动文本：每次回车都无脑重写一遍会把撤销栈和选区
        搅乱，也会让本来正确的「1,2,3 回车」多出一次无意义的写库。
        """
        try:
            ln = int(ln)
        except (TypeError, ValueError):
            return False
        base = self._numbered_line_info(ln)
        if base is None:
            return False
        base_indent = len(base[1])

        def depth(n):
            """该行的缩进深度；空行返回 None（空行是列表的硬边界）。"""
            info = self._numbered_line_info(n)
            if info is not None:
                return len(info[1])
            try:
                txt = self.text.get("%d.0" % n, "%d.end" % n)
            except Exception:
                return None
            if not txt.strip():
                return None
            return len(txt) - len(txt.lstrip("\u3000 "))

        def continues(n):
            """n 行是否还在这个列表块里。

            比基准缩进更深的行（子列表 / 缩进续写）**不打断**父级编号：
            1. 父项
               1. 子项
            2. 父项
            重编父级时要跨过子项继续数 1、2，而不是数到 1 就停。
            """
            d = depth(n)
            if d is None:
                return False
            if self._numbered_line_info(n) is None:
                return d > base_indent
            return d >= base_indent

        top = ln
        while continues(top - 1):
            top -= 1
        bottom = ln
        while continues(bottom + 1):
            bottom += 1
        changes = []
        i = 0
        for n in range(top, bottom + 1):
            info = self._numbered_line_info(n)
            if info is None or len(info[1]) != base_indent:
                continue
            i += 1
            prefix = info[0]
            new_prefix = renumber_prefix(prefix, i)
            if new_prefix != prefix:
                changes.append((n, prefix, new_prefix))
        if not changes:
            return False
        self.text.config(state=tk.NORMAL)
        if sep:
            self.text.edit_separator()
        for n, prefix, new_prefix in changes:
            self.text.delete("%d.0" % n, "%d.0 +%dc" % (n, len(prefix)))
            self.text.insert("%d.0" % n, new_prefix, ())
        if sep:
            self.text.edit_separator()
        return True

    def _indent_len_at(self, ln):
        """该行行首的缩进字符数（全角空格 / 半角空格都算一格）。"""
        try:
            txt = self.text.get("%d.0" % ln, "%d.end" % ln)
        except Exception:
            return 0
        return len(txt) - len(txt.lstrip("\u3000 "))

    def _nearest_numbered_at_indent(self, ln, indent):
        """从 ln 往两边找最近的一条**该缩进层级**的有序列表行。

        删掉一行后，原来那一行现在装着它的下一行（可能是个更深的子项）。
        直接拿当前行去重编会编错层级 —— 删父项时该继续编的是后面的父项，
        不是顶上来的子项。这里按「被删行的缩进」找同级的那一条。
        """
        try:
            last = int(self.text.index("end-1c").split(".")[0])
        except Exception:
            return None
        for step in (1, -1):
            n = ln if step > 0 else ln - 1
            while 1 <= n <= last:
                info = self._numbered_line_info(n)
                if info is not None and len(info[1]) == indent:
                    return n
                try:
                    txt = self.text.get("%d.0" % n, "%d.end" % n)
                except Exception:
                    break
                if not txt.strip():
                    break                       # 空行 = 列表块结束
                if info is not None and len(info[1]) < indent:
                    break                       # 更浅的列表 = 已经走出这一层
                if info is None and self._indent_len_at(n) < indent:
                    break
                n += step
        return None

    def _common_list_kind(self):
        """当前光标 / 选区所有行共同的列表类型；混合或普通正文返回 None。"""
        lines = [ln for ln in self._touched_lines()
                 if str(ln) != self.TITLE_LINE]
        if not lines:
            return None
        kinds = [self._list_kind_at(ln) for ln in lines]
        if kinds[0] is None or any(k != kinds[0] for k in kinds):
            return None
        return kinds[0]

    def renumber_selected_lists(self):
        """右键菜单「重新编号」：把选区覆盖到的有序列表块从头重编一遍。"""
        if not self.current_note() or self._refuse_readonly():
            return
        lines = [ln for ln in self._touched_lines()
                 if str(ln) != self.TITLE_LINE]
        if not lines or not any(self._list_kind_at(ln) == "numbered"
                                for ln in lines):
            return
        self.text.config(state=tk.NORMAL)
        self.text.edit_separator()
        try:
            for ln in lines:
                self._renumber_numbered_block(ln, sep=False)
        finally:
            self.text.edit_separator()
        self._sync_list_indent()
        self.mark_modified()

    def insert_code(self, lang=""):
        """在当前光标处插入一个代码块：整块独占若干行，插好即把光标放进代码里。

        若当前有选中文字，则把选中的内容直接变成代码（免得先删再粘）。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        picked = ""
        sel = self.text.tag_ranges("sel")
        if sel:
            picked = self.text.get(sel[0], sel[1]).rstrip("\n")
            self.text.delete(sel[0], sel[1])
        # 代码块要独占行：光标不在行首时先补个换行，免得和前面的正文挤在同一行
        if self.text.index("insert").split(".")[1] != "0":
            self.text.insert(tk.INSERT, "\n")
        blk = self._add_code_block(lang=lang, code=picked, index=tk.INSERT)
        self.text.insert(tk.INSERT, "\n")     # 块后留一行，方便继续写正文
        self.mark_modified()
        blk.focus_code()

    def _add_code_block(self, lang="", code="", index=tk.END):
        blk = CodeBlock(self.text, self, lang=lang, code=code)
        self._codes.append(blk)
        self.text.window_create(index, window=blk, padx=0, pady=dpi.px(6))
        # 宽度必须先定：高度是按「这个宽度下代码的实际显示行数」算出来的
        blk.set_width(self._code_width(), fit=False)
        self._after_idle(blk.fit)
        return blk

    def _remove_code_block(self, blk):
        """删除一个代码块（连同它后面那个换行，免得留下一整行空白）。"""
        if self._refuse_readonly():
            return
        try:
            idx = self.text.index(blk)
        except Exception:
            return
        self.text.config(state=tk.NORMAL)
        self.text.delete(idx)          # 删掉嵌入式窗口本身（Tk 会销毁该控件）
        try:
            if self.text.get(idx, "%s +1c" % idx) == "\n":
                self.text.delete(idx)
        except Exception:
            pass
        try:
            self._codes.remove(blk)
        except ValueError:
            pass
        self.mark_modified()

    def _title_line_break(self):
        """首行（标题行）回车：新行自动切成正文样式，标题只留第一行。

        Tk 的插入规则：插入点落在标签范围「中间」会继承该标签，落在「末尾」不会。
        所以光标在首行末尾回车时新行本来就没有 title；但光标停在标题中间时，
        后半段文字会连同行级标签一起落到新行 —— 这里显式把新行的标题标签换成正文。
        """
        sel = self.text.tag_ranges("sel")
        if sel:
            self.text.delete(sel[0], sel[1])
        self.text.insert(tk.INSERT, "\n")
        ls, le = "2.0", "2.end"
        self.text.tag_remove("title", ls, le)
        self.text.tag_remove("h2", ls, le)
        self.text.tag_remove("quote", ls, le + " +1c")   # 首行是引用时，新行同样要断开
        self.text.tag_add("body", ls, le)
        self.mark_modified()
        return "break"

    def _fresh_check_mark(self, marker):
        """续接待办时用的新标记：**保留行首缩进**，勾选态一律重置成未勾的 ⬜。

        缩进要留着（同一组待办才对齐），勾选态不能留（见 _editor_return 里的说明）。
        标记里的空白统一收敛成一个 NBSP，与工具栏直接插入的形态逐字符一致 ——
        两处出来的待办长得分毫不差，用户才不会再觉得「回车出来的和插进去的不是一回事」。

        当前行没有缩进（老笔记，或早先贴行首插的待办）时补上现行缩进：
        续接出来的新行从此和项目符号 / 三角列表对齐，这是老待办唯一的迁移途径。
        间隙一律用 TODO_GAP（两个正文宽空格），与工具栏插入的形态逐字符一致。
        """
        lead = ""
        for ch in (marker or ""):
            if ch in TODO_MARKS:
                break
            lead += ch
        return (lead or LI_INDENT) + TODO_OFF + TODO_GAP

    def _line_prefix(self, ln):
        """返回当前行开头的列表标记类型与文本：('bullet'/'triangle'/'numbered'/'check', marker)。"""
        try:
            st = self.text.dump(ln + ".0", ln + ".end", window=True, text=True)
        except Exception:
            st = []
        first = None
        for key, value, _idx in st:
            if key in ("text", "window"):
                first = (key, value)
                break
        if first is None:
            return (None, "")
        if first[0] == "window":
            # 代码块和分割线也是 window，但它们不是待办：回车交给默认行为
            # （待办的勾选框现在是字面字符，不再走窗口这条路）
            return (None, "")
        txt = first[1]
        # 列表标记连同行首缩进一起拆出来：新行是「缩进 + 标记」，存量旧行
        # 没有缩进，两种都要认（缩进也是正文的一部分，见 widgets.LI_INDENT）。
        # 取整行文本而不是 dump 的第一段：行首若因加粗等标签被切开，dump 的
        # 第一段可能只有缩进，标记落在第二段里就漏判了。
        try:
            return split_list_prefix(self.text.get(ln + ".0", ln + ".end"))
        except Exception:
            return split_list_prefix(txt)

    def _window_at_line_start(self, ln):
        target = ln + ".0"
        for wname in self.text.window_names():
            try:
                if self.text.index(wname) == target:
                    return self.text.nametowidget(wname)
            except Exception:
                pass
        return None

    _INDENT_SPACES = " \u3000\u00a0"

    def _indent_run_len(self, txt):
        """一段文本里「一档缩进」的字符数：Tab 优先；否则最多 4 个半角/全角空格。

        两格全角空格是列表的 LI_INDENT，四个半角空格是粘贴进来的代码缩进，
        两者都算一档（正文 Tab 宽 = 2 汉字 ≈ 4 半角）。
        """
        if txt.startswith("\t"):
            return 1
        n = 0
        for ch in txt[:4]:
            if ch not in self._INDENT_SPACES:
                break
            n += 1
        return n

    def _editor_tab_maybe_shift(self, event):
        """<Tab> 兜底：Shift 位亮着就当反向 Tab 处理，否则放行原本的插 Tab 行为。"""
        if self._refuse_readonly():
            return "break"              # <Tab> 顶掉了 <Key> 守卫，这里补上
        if getattr(event, "state", 0) & 0x1:
            return self._editor_shift_tab(event)
        return None

    def _editor_shift_tab(self, event=None):
        """Shift+Tab：删掉一档缩进（Tab / 4 半角空格 / 2 全角空格，或选中行首）。

        一律真删字符、不是改 tag：Tab 插进正文的是字面 `\\t`，列表用的是两格全角，
        粘贴的代码又可能是四个半角，三种都按一档处理。撤一次 Ctrl+Z 就能回来。
        多行选区按行首 outdent，自下而上删，免得前面的行号被带偏。
        """
        if self._refuse_readonly():
            return "break"
        t = self.text
        if not self.current_note() or str(t.cget("state")) == tk.DISABLED:
            return "break"

        def _dedent_line(ln):
            """删这一行行首的一档缩进，返回删掉的字符数。"""
            txt = t.get("%d.0" % ln, "%d.end" % ln)
            n = self._indent_run_len(txt)
            if n:
                t.delete("%d.0" % ln, "%d.%d" % (ln, n))
            return n

        sel = t.tag_ranges("sel")
        if sel:
            fl, fc = (int(x) for x in str(sel[0]).split("."))
            ll, lc = (int(x) for x in str(sel[1]).split("."))
            # 「选中到第 N+1 行行首」是常见的整行选法，那一行并没有被选到
            if lc == 0 and ll > fl:
                ll -= 1
                lc = len(t.get("%d.0" % ll, "%d.end" % ll))
            removed = {}

            def _dedent_all():
                for ln in range(ll, fl - 1, -1):
                    removed[ln] = _dedent_line(ln)

            self._find_edit_group(_dedent_all)
            # 选区跟着文字一起收，别让退完缩进后选中的还是原来那几个字符位置
            t.tag_remove("sel", "1.0", "end")
            t.tag_add("sel", "%d.%d" % (fl, max(0, fc - removed.get(fl, 0))),
                      "%d.%d" % (ll, max(0, lc - removed.get(ll, 0))))
        else:
            ln, col = (int(x) for x in t.index("insert").split("."))
            before = t.get("%d.0" % ln, "%d.%d" % (ln, col))
            n = self._indent_run_len(before[::-1])   # 倒过来查光标前的那一段
            if n:
                t.delete("%d.%d" % (ln, col - n), "insert")
            else:
                # 光标不在缩进尾巴上（含行首）：按常规 outdent 删行首一档
                _dedent_line(ln)
        self._sync_list_indent()
        return "break"

    def _editor_return(self, event):
        """回车：若当前行是列表/待办，自动续接同格式；空列表项回车则结束列表。"""
        # 候选面板开着时，回车是「选中」而不是换行（面板没开就照原样走下去）
        if self._mention_accept():
            return "break"
        if not self.current_note():
            return None
        # 只读笔记里 Return 有单独绑定，绕过了 <Key> 守卫，这里再挡一次
        if self._refuse_readonly():
            return "break"
        self.text.config(state=tk.NORMAL)
        ln = self.text.index("insert").split(".")[0]
        # ⚠️ 整行图片语法必须**最先**判：`![alt](src)` 里的 `[alt](src)` 会被
        # 后面的 _convert_md_links 当成 markdown 链接拆掉（MD_LINK_RE 不认前面
        # 那个 `!`），等轮到图片判断时行已经变了，永远是「回车没反应」。
        img_line = self.text.get(ln + ".0", ln + ".end")
        md_img = imgstore.parse_md_image(img_line)
        if md_img is not None:
            return self._img_line_break(ln, md_img[1], md_img[0])
        # 行尾刚敲完 date() / datetime() / [文字](url)：回车同样要展开，
        # 否则"敲空格才生效"会显得功能时灵时不灵
        if self._expand_time_token(ln):
            ln = self.text.index("insert").split(".")[0]
        if self._convert_md_links(ln):
            ln = self.text.index("insert").split(".")[0]
        # 这一行刚打完网址（回车收尾）→ 顺手标成可点击链接
        if "http" in self.text.get(ln + ".0", ln + ".end"):
            self._mark_line_links(ln)
        kind, marker = self._line_prefix(ln)
        # 第一行恒为标题行：回车一律按「标题行回车」处理 —— 新行切成正文样式，
        # 也不续接任何标记（存量笔记里第一行带列表标记的，敲一次回车就自然
        # 脱开，不必为它单独写分支）。
        if str(ln) == self.TITLE_LINE:
            return self._title_line_break()
        if kind is None:
            txt = self.text.get(ln + ".0", ln + ".end")
            # 「空行上刚转成标题/引用」记下的待补样式，只在原地接着打字时才兑现；
            # 按了回车就作罢 —— 否则隔一行再打字会被上一行的标记追着染上。
            pend = getattr(self, "_md_pending", None)
            if pend and str(pend[0]) == ln:
                self._md_pending = None
                if pend[1] == "quote" and not txt:
                    # 这是上一次回车留下的「续接待命空行」（见 _quote_continue_break）。
                    # 再按一次回车 = 跳出引用块：把占位空行连换行符一起消费掉，
                    # 否则正文里会白留一条空行。
                    return self._quote_exit_break(ln)
            # 整行只有 --- / *** / ___：换成一条分割线（markdown 的写法）
            if is_hr(txt):
                return self._hr_line_break(ln)
            # 整行只有 ``` 加可选语言名（```python）：按回车生成代码块。
            # 与「空格触发」并存：想立刻确认的可以敲空格，习惯回车的就是回车。
            fence = parse_fence(txt)
            if fence is not None:
                return self._fence_line_break(ln, fence)
            # 引用行回车：续接引用（新行继承 quote）；**空**引用行回车 = 跳出。
            # 「连续两次回车跳出引用」：第一次在引用行尾回车得到空引用行（色块
            # 仍在），紧接着再回一次车就退出引用块。
            if self._line_has_tag("quote", ln) and not txt:
                return self._quote_exit_break(ln)
            if self._line_has_tag("quote", ln):
                # 引用行回车要**显式**续接：Tk 只在标签范围中间插入时才继承标签，
                # 而新行的起点正好是上一行 quote 范围的末端 —— 靠继承一定掉。
                # 以前是靠「空行保留色块」把标签提前挂上，现在色块取消了，
                # 续接必须自己写（见 _quote_continue_break）。
                return self._quote_continue_break(ln)
            return  # 普通回车：交给 Tk 默认行为
        line_txt = self.text.get(ln + ".0", ln + ".end")
        if kind == "check":
            body = line_txt[len(marker):].strip() if marker else line_txt.strip()
            if body == "":
                # 空待办行 → 结束列表：删掉整个标记（方框 + 间隙），不留空框
                if marker:
                    self.text.delete(ln + ".0", ln + ".%d" % len(marker))
                self.mark_modified()
                return
            # 续接待办：新行同样是「方框 + NBSP」，**标签也要一起带上** ——
            # todo / todo_done 不只管字号颜色，点击切换的 tag_bind 就挂在它们身上。
            # 早先这里只插字面量，续接出来的框是没有标签的裸字符：渲染出来比
            # 工具栏插的小一号、颜色也淡，而且点了完全没反应（正是用户报的
            # 「回车之后和直接插入的不一致，且无法打钩」）。
            # 勾选态**不跟着续接**：新行是下一条待办，不是上一条的副本。照抄 ✅ 会让
            # 新项一出生就是「已完成」，而 done 的删除线只在点击/载入时才画，屏幕上
            # 会出现「绿框配正常文字」的半成品状态。
            self.text.insert(tk.INSERT, "\n" + self._fresh_check_mark(marker))
            self._tag_todo_prefix(self.text.index("insert").split(".")[0])
            self.mark_modified()
            return "break"
        # 去掉行首标记，判断是否为空列表项
        strip_body = line_txt[len(marker):] if (marker and line_txt.startswith(marker)) else line_txt
        if strip_body.strip() == "":
            # 空列表项 → 结束列表：删除标记，保留普通换行
            if marker:
                self.text.delete(ln + ".0", ln + ".%d" % len(marker))
            # 删掉的是有序列表里的空项时，它后面的项要顶上来（1,2,空,4 →
            # 1,2,3）。当前行已经变成普通空行，所以从下一行开始找块。
            self._renumber_numbered_block(int(ln) + 1, sep=False)
            self.mark_modified()
            return
        # 续接同格式。marker 带着当前行原有的缩进（新行有「缩进 + 标记」，
        # 存量旧行只有标记），原样拼回去，同一组列表的缩进才不会忽有忽无。
        if kind == "triangle":
            self.text.insert(tk.INSERT, "\n" + (marker or TRI_MARK))
        elif kind == "bullet":
            self.text.insert(tk.INSERT, "\n" + (marker or BULLET_MARK))
        elif kind == "numbered":
            # 序号 +1，其余（缩进、标记后的空格数）原样保留
            m = re.search(r"(\d+)\.", marker or "")
            cur = int(m.group(1)) if m else 1
            nxt = re.sub(r"\d+", str(cur + 1), marker or "", count=1) or NUM_MARK
            self.text.insert(tk.INSERT, "\n" + nxt)
            # 在列表中间回车会把后面几项整体顶下去：1,2,3 里在第 1 项后回车
            # 得到 1,2,2,3。这里把整块重编一遍，落成 1,2,3,4（P1 自动重编号）。
            self._renumber_numbered_block(
                self.text.index("insert").split(".")[0])
        self.mark_modified()
        return "break"

    # ---------- markdown 输入快捷 ----------
    # 三条设计约束（都是踩过的坑）：
    #   1) 触发点是「空格已经落进正文之后」，不是 <<Modified>> —— 那里拿不到按键，
    #      而且每击键都跑一遍；空格后用 idle 回调，一次只查当前行。
    #   2) 转换前后各打一条 edit_separator()。Tk 的 undo 是字符级的，不打边界的话
    #      Ctrl+Z 会变成「一个一个字符地退」，用户会以为功能坏了。
    #   3) 只认行首前缀，认不出就立刻返回，代价 O(行长)。

    def _md_after_space(self, event=None):
        """<space> 的监听：等空格插进正文后再判断行首。**不能 return "break"**。

        行号在**登记时**就记下来：idle 真正执行时，光标可能已经被后续击键
        带到别处（连续快速输入时很常见），那时再用 insert 定位就会扫错行。
        """
        if not self.current_note() or self.is_readonly():
            return None
        if self._col_active():      # 列模式：别拿这一列去判 markdown 前缀
            return None
        job = getattr(self, "_md_job", None)
        if job is not None:
            try:
                self._cancel_after(job)
            except Exception:
                pass
        try:
            idx = self.text.index("insert")     # 空格**插入前**的光标位置
            ln = str(idx).split(".")[0]
        except Exception:
            return None
        self._md_job = self._after_idle(lambda: self._md_convert_prefix(ln, idx))
        return None

    def _md_after_paren(self, event=None):
        """敲完 `)`：整行 `![alt](url)` 就地渲染，不必再按回车。

        和「空格触发」共用同一条 idle 链（`_md_convert_prefix` 第一件事就是判
        整行图片），两个入口必须同一口径。**不能 return "break"**：`)` 还得先
        插进正文，转换在 idle 里做 —— 那会儿插入已经落定。
        """
        if not self.current_note() or self.is_readonly():
            return None
        try:
            idx = self.text.index("insert")
            ln = str(idx).split(".")[0]
        except Exception:
            return None
        self._md_job = self._after_idle(lambda: self._md_convert_prefix(ln, idx))
        return None

    def _md_after_paste(self, event=None):
        """粘贴后补一次整行图片识别：贴进来的 `![](url)` 直接变图。

        默认粘贴发生在 class binding 里（本函数跑在它**之前**），所以只能等
        idle 再看正文。**不能 return "break"**，否则粘贴本身就被吞掉。
        """
        if not self.current_note() or self.is_readonly():
            return None
        self._after_idle(self._md_image_after_paste_check)
        return None

    def _md_image_after_paste_check(self):
        """粘贴落定后：当前行（含剪贴板带换行时的上一行）是不是整行图片语法。"""
        if not self.current_note() or self.is_readonly():
            return
        try:
            ln, col = self.text.index("insert").split(".")
        except Exception:
            return
        cands = [ln]
        if col == "0" and int(ln) > 1:
            # 剪贴板里带结尾换行（复制一整行时很常见）：语法落在上一行
            cands.insert(0, str(int(ln) - 1))
        for n in cands:
            try:
                line = self.text.get(n + ".0", n + ".end")
            except Exception:
                continue
            if imgstore.parse_md_image(line) is not None:
                self._md_convert_prefix(n)
                return

    def _md_convert_prefix(self, ln=None, idx=None):
        """idle 回调：真正做转换的地方。"""
        self._md_job = None
        if not self.current_note() or self.is_readonly():
            return
        try:
            ln = ln or self.text.index("insert").split(".")[0]
            line = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return
        # 网址识别与块级前缀无关，每行都查一次（打完半角空格即变蓝）
        # 顺序有讲究：先展开时间函数与富链接（它们会改写行内文字），再重新取一遍
        # 行文本做前缀判断，否则判断用的是改写前的内容。
        # 整行图片语法优先：![alt](src) 里的 [alt](src) 会被 _convert_md_links
        # 抢先当成普通链接拆掉（和回车入口同一个坑）。
        img_line = self.text.get(ln + ".0", ln + ".end")
        md_img = imgstore.parse_md_image(img_line)
        if md_img is not None and self.current_note():
            self._img_line_break(ln, md_img[1], md_img[0])
            return
        if self._expand_time_token(ln):
            return
        # `$5+6=` 敲完空格 → 删掉 `$` 并补成 `5+6= 11`（见 calc.py）。放在前缀判断之前：
        # 它只改行内文字，不改行首结构。
        if self._expand_formula(idx):
            return
        if self._convert_md_links(ln):
            return
        self._mark_line_links(ln)
        try:
            line = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return
        kind, consume, extra = detect_prefix(line)
        if kind is None:
            return
        # 第一行是标题行：列表类前缀**静默不转换**（- / 1. / -> / -[] 原样留在
        # 行首当普通文字）。这里不弹提示 —— 打字过程中弹模态框会抢走焦点，
        # 用户还得再点回正文。工具栏按钮那边才提示。
        if str(ln) == self.TITLE_LINE and kind in ("bullet", "numbered",
                                                   "triangle", "check"):
            return
        self.text.edit_separator()
        try:
            self._apply_md_prefix(ln, kind, consume, extra)
        finally:
            self.text.edit_separator()

    def _apply_md_prefix(self, ln, kind, consume, extra):
        """把识别出来的前缀换成编辑器里已有的形态。"""
        self.text.config(state=tk.NORMAL)
        if kind in ("title", "h2"):
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.apply_line_tag(kind)
            return
        if kind == "quote":
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.toggle_quote()
            return
        if kind == "bullet":
            # 项目符号沿用编辑器自己的 "●␣␣"，不是 markdown 的 "- "：
            # 视觉上跟工具栏按钮插出来的一致，导出时再换回 "- "。
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.text.insert(ln + ".0", BULLET_MARK)
            self.mark_modified()
            return
        if kind == "triangle":
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.text.insert(ln + ".0", TRI_MARK)
            self.mark_modified()
            return
        if kind == "check":
            # `-[]` + 空格 → 待办：删掉三个字符，走和工具栏同一条插入路径
            # （_add_checkbutton 会补 ⬜ + 间隙 NBSP，并挂上 todo 标签 ——
            #  裸插字符的话框点了不会响应，见 _tag_todo_prefix 的说明）。
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self._add_checkbutton(checked=False, index=ln + ".0")
            self.mark_modified()
            return
        if kind == "numbered":
            # 补成「缩进 + 序号 + 不换行空格」，与工具栏/回车续接的形态一致
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.text.insert(ln + ".0", LI_INDENT + "%d.%s" % (extra or 1, _MARK_NBSP))
            # 在列表中间敲出一个新序号（比如 1,2 之间敲 "5. "）时整块重编，
            # 落成 1,2,3 而不是 1,5,2（P1 自动重编号）。
            self._renumber_numbered_block(ln, sep=False)
            self.mark_modified()
            return
        if kind == "fence":
            self.text.delete(ln + ".0", ln + ".end")
            self.insert_code(lang=(extra or ""))
            return
        if kind == "hr":
            self.text.delete(ln + ".0", ln + ".end")
            self._add_hr(ln + ".0")
            self.text.mark_set("insert", "%d.0" % (int(ln) + 1))
            self.mark_modified()

    def _quote_exit_break(self, ln):
        """空引用行上回车：跳出引用块。

        引用行回车会续接（新行继承 quote），想退出就把当前引用行的字删光再回
        一次车 —— 这里消费掉这个空行（连换行符一起删），光标落到下一行行首，
        引用块到此结束。空行已是文本最后一行时换行符受 Tk 保护删不掉，退一档：
        只摘掉该行的 quote，光标停在普通空行上。

        注：现在空行已经不再保留 quote（见 _sync_quote_line），正常情况下
        _editor_return 走不到这里；留着它是为了兜住「色带还没被清掉就按了回车」
        这种时序，删了会让那种情形下多出一次空回车。
        """
        ls = ln + ".0"
        self.text.edit_separator()
        try:
            if self.text.compare(ls + " +1c", "<=", "end-1c"):
                self.text.delete(ls, ls + " +1c")     # 空行不是最后一行 → 整行消费
            else:
                self.text.tag_remove("quote", ls, ls + " +1c")
        except Exception:
            pass
        self.text.edit_separator()
        self.mark_modified()
        return "break"

    def _quote_continue_break(self, ln):
        """引用行上回车：新行接着当引用（引用块没结束）。

        空行**不挂色块**了（「引用删掉后引用色不必保留」），所以这里也不能直接
        tag_add —— 那会在新行上画出一条删不掉的灰带。改成记一笔待补样式：
        空行干干净净，等第一个字进来时由 _apply_pending_line_tag 补成引用。
        这样一来「引用行尾回车 → 空行 → 再回车」仍然是跳出引用块，
        而「回车后立刻打字」仍然是引用，两种意图都保留。

        光标在行中时是**拆分**：后半段跟着新行走，整行直接按引用重设。
        """
        self.text.edit_separator()
        try:
            self.text.insert(tk.INSERT, "\n")
            nl = self.text.index("insert").split(".")[0]
            if self.text.get(nl + ".0", nl + ".end"):
                self._set_line_tag(nl, "quote")
            else:
                # 只记待补样式，不动标签 —— 空行因此没有底色
                for t in self.LINE_TAGS:
                    self.text.tag_remove(t, nl + ".0", nl + ".end +1c")
                self._md_pending = (nl, "quote")
        finally:
            self.text.edit_separator()
        self.mark_modified()
        return "break"

    def _hr_line_break(self, ln):
        """整行只有 --- 时按回车：把它换成一条分割线（而不是留一行原文）。"""
        self.text.edit_separator()
        self.text.delete(ln + ".0", ln + ".end")
        self._add_hr(ln + ".0")
        self.text.mark_set("insert", "%d.0" % (int(ln) + 1))
        self.text.edit_separator()
        self.mark_modified()
        return "break"

    # ---------- 图片（Markdown 整行语法 / 粘贴） ----------
    # 一张图在正文里是结构项 ["img", {id, src, w, h, alt}]，真实文件在
    # data/media/<hash>.png；src 只存相对路径或图床 URL，绝不存绝对路径。
    # 数据块的落库认领走 serialize() 的 image 分支（和闹钟同一条 dump 链）。

    def _img_bytes_from_source(self, src):
        """src → (图片字节, w, h)。本地路径读文件，http(s) 下载。失败抛异常。

        w/h 只对本地位图有意义；网络图拿不到就给 0，渲染时按实际加载到的尺寸走。
        """
        src = str(src or "").strip().strip('"')
        if imgstore.is_remote(src):
            # 统一走 imgstore.fetch_bytes：https 握手被掐时它会自动降级 http 重试
            return imgstore.fetch_bytes(src), 0, 0
        # 本地：**先按 imgstore 的规范路径解释**（media/<hash>.png 这类，和渲染
        # 用的是同一个函数，两边口径必须一致 —— 否则「正文里能显示的图」在
        # 重新插入 / 从 Markdown 导入时反而报「找不到图片文件」）。
        import store as _store
        p = imgstore.src_path(src)
        if not p:
            # 用户手输的任意路径：相对路径按「项目根」解释（不是 cwd —— 双击
            # exe 启动时 cwd 可能是桌面）
            p = os.path.expanduser(src)
            if not os.path.isabs(p):
                p = os.path.join(os.path.dirname(_store.BASE), p)
        if not os.path.isfile(p):
            raise ValueError("找不到图片文件：%s" % src)
        with open(p, "rb") as f:
            return f.read(), 0, 0

    def _img_make_block(self, data, alt="", w=0, h=0, ident=None):
        """图片字节 → img 数据块（落 media/）。缩小上限走 imgstore 默认值。"""
        return imgstore.make_block(data, alt=alt, base=None,
                                   width=w, height=h, ident=ident)

    # 整行图片语法「下载中」的占位文字与标签。标签进 _is_ephemeral_tag ——
    # 占位只是过程态，样式不该跟着正文落库。（真值定义在模块级，见那里的说明）
    IMG_LOADING_TAG = IMG_LOADING_TAG
    IMG_LOADING_TEXT = IMG_LOADING_TEXT

    def _img_line_break(self, ln, src, alt):
        """整行 `![alt](src)` 敲完 `)` / 粘贴 / 回车：换成图片块。

        undo 边界照抄 _hr_line_break：转换前后各一条 edit_separator，Ctrl+Z
        一步退回原文，而不是一个字符一个字符地退。
        """
        # 远程图分两路：缓存里已有就直接读文件（同步是安全的，毫秒级）；
        # 没有就排后台下载 —— ⚠️ 绝不在主线程上同步下网络图：慢网时整窗假死，
        # https 被掐时还要白等 20 秒超时（见 imgstore.fetch_bytes 的说明）。
        if imgstore.is_remote(src):
            cached = self._img_cache_read(src)
            if cached:
                try:
                    with open(cached, "rb") as f:
                        block = self._img_block_remote(src, alt, f.read())
                except Exception as e:
                    self._show_error("插入图片失败", "读不到这张图片：\n%s" % e)
                    return None
                return self._img_line_place(ln, block, src, alt)
            return self._img_line_break_remote(ln, src, alt)
        try:
            data, w, h = self._img_bytes_from_source(src)
            block = self._img_make_block(data, alt=alt, w=w, h=h)
        except imgstore.UnsupportedImage as e:
            self._show_error("插入图片失败", str(e))
            return None
        except Exception as e:
            self._show_error("插入图片失败", "读不到这张图片：\n%s" % e)
            return None
        return self._img_line_place(ln, block, src, alt)

    def _img_block_remote(self, src, alt, png):
        """远程图字节 → img 数据块。src 保留 URL，**不**在 media/ 里留副本。

        远程图刻意不复制进 media/：用户要的是「改地址就换图」，复制一份本地
        副本会让点开图片时看到本地路径、换地址还得改两处；缓存目录里已经存了
        一份归一化 PNG 供渲染用，够离线看了。
        """
        png, w, h = imgstore.normalize_image(png)
        self._img_cache_put(src, png)
        return ["img", {"id": imgstore.img_id(), "src": src,
                        "w": w, "h": h, "alt": str(alt or "")}]

    def _img_line_place(self, ln, block, src, alt):
        """把 ln 整行换成图片块。失败就把原文放回这一行，不让内容凭空消失。"""
        self.text.edit_separator()
        self.text.delete(ln + ".0", ln + ".end")
        self.text.mark_set("insert", ln + ".0")
        if not self._img_add(ln + ".0", block):
            self.text.insert(ln + ".0", "![%s](%s)" % (alt, src))
            self.text.edit_separator()
            return None
        # 图元不占字符：如果这一行是笔记最后一行（原来末尾没有换行），换完图光标
        # 就贴在图元右边、无行可去 —— 用户接着打字会变成「图 + 正文」挤在一行。
        # 补一个换行，让光标稳稳落到下一行（和回车插入图片的手感一致）。
        if self.text.compare("%d.end" % int(ln), ">=", "end-1c"):
            self.text.insert("%d.end" % int(ln), "\n")
        self.text.mark_set("insert", "%d.0" % (int(ln) + 1))
        self.text.edit_separator()
        self.mark_modified()
        self._img_after_insert(block)      # 图床模式的上传钩子（阶段④接线）
        return "break"

    def _img_line_break_remote(self, ln, src, alt):
        """远程图：先把这行换成「图片加载中…」，后台下完再换成真图。

        占位是必要的 —— 下载要好几秒，什么都不变会让用户以为「回车没反应」。
        占位文字挂 img_loading 标签（ephemeral，不落库），并用一个 mark 记住
        位置、记下此时是哪条笔记；回调时三样都对上才动手（mark 还在、那一行
        仍是占位、笔记没切走），否则一律放弃 —— 绝不覆盖用户已经改成的别的内容。
        """
        nid = (self.current_note() or {}).get("id")
        mark = "imgsrc_%s" % imgstore.img_id()
        try:
            self.text.edit_separator()
            self.text.delete(ln + ".0", ln + ".end")
            self.text.insert(ln + ".0", self.IMG_LOADING_TEXT,
                             (self.IMG_LOADING_TAG,))
            self.text.mark_set(mark, ln + ".0")
            self.text.mark_gravity(mark, "left")
            self.text.mark_set("insert", "%d.end" % int(ln))
            self.text.edit_separator()
        except tk.TclError:
            return None
        self._img_remote_async(
            src, lambda path, err: self._img_line_break_done(
                mark, path, err, nid, src, alt))
        return "break"

    def _img_line_break_done(self, mark, path, err, nid, src, alt):
        """后台下载回来了：占位换真图；失败就把原文放回去并说清原因。"""
        cur = self.current_note() or {}
        if nid and cur.get("id") != nid:
            return                        # 期间切走了笔记，这行已不归我管
        try:
            if mark not in self.text.mark_names():
                return
            ln = str(self.text.index(mark)).split(".")[0]
            if self.text.get(ln + ".0", ln + ".end") != self.IMG_LOADING_TEXT:
                return                    # 用户改了这一行：别覆盖他的内容
        except tk.TclError:
            return
        if not path:
            self._img_restore_line(ln, mark, src, alt)
            self._show_error("插入图片失败",
                             "读不到这张图片：\n%s" % (err or src))
            return
        try:
            with open(path, "rb") as f:
                block = self._img_block_remote(src, alt, f.read())
        except Exception as e:
            self._img_restore_line(ln, mark, src, alt)
            self._show_error("插入图片失败", "读不到这张图片：\n%s" % e)
            return
        try:
            self.text.mark_unset(mark)
        except tk.TclError:
            pass
        self._img_line_place(ln, block, src, alt)

    def _img_restore_line(self, ln, mark, src, alt):
        """占位 → 原文 `![alt](src)`（下载失败时不留「图片加载中」这种假状态）。"""
        try:
            self.text.edit_separator()
            self.text.delete(ln + ".0", ln + ".end")
            self.text.insert(ln + ".0", "![%s](%s)" % (alt, src))
            self.text.mark_set("insert", "%d.end" % int(ln))
            self.text.edit_separator()
        except tk.TclError:
            return
        try:
            self.text.mark_unset(mark)
        except tk.TclError:
            pass


    def _img_after_insert(self, block):
        """插入图片后的钩子：配置了 PicList 时后台上传并改用图床 URL。

        插入与上传解耦 —— 图已经嵌进正文、落库正常；上传在后台进行，成功后
        **就地**把这一枚的 src 换成 URL、删掉本地文件，失败就保持本地图并轻提示。
        不做同步等待，界面不卡。
        """
        settings = (self.data.get("settings") or {}) if getattr(self, "data", None) else {}
        if not piclist.configured(settings):
            return
        try:
            d = block[1]
            src = str(d.get("src") or "")
        except Exception:
            return
        path = imgstore.src_path(src)
        if not path or not os.path.isfile(path):
            return                      # 网络图 / 文件已不在：不必再传
        note = self.current_note()
        nid = note.get("id") if note else None
        ident = str(d.get("id") or "")

        def work():
            return piclist.upload(settings, path)

        def done(res):
            ok, value = res if isinstance(res, tuple) else (True, res)
            if not ok:
                # 上传失败不影响本地图：弹一次说明（插图低频，用户需要知道
                # 图没上图床），失败图仍好好地躺在 data/media/ 里。
                self._show_info("图片已存本地", "图床上传失败，图片已保存在本地：\n%s" % value)
                return
            self._img_switch_to_remote(nid, ident, value, src)

        self._img_async(work, done)

    def _img_switch_to_remote(self, nid, ident, url, old_src):
        """把某张图从本地切到图床 URL：改数据块 → 重嵌图片 → 删本地文件。"""
        note = next((n for n in self.data.get("notes") or []
                     if n.get("id") == nid), None)
        if note is None:
            return
        changed = False
        for it in note.get("content") or []:
            if not (isinstance(it, list) and len(it) > 1 and it[0] == "img"):
                continue
            d = it[1]
            if not isinstance(d, dict) or str(d.get("id")) != ident:
                continue
            if str(d.get("src")) != old_src:
                continue                 # 期间被用户改过，别覆盖
            d["src"] = url
            changed = True
            break
        if not changed:
            return
        # 正文里那枚图元也要换成图床图；只在它仍是当前打开笔记时处理
        cur = self.current_note()
        if cur and cur.get("id") == nid:
            name = "img:" + ident
            try:
                idxs = [i for i in self.text.image_names() if i == name]
                if idxs:
                    # 重新造一张 PhotoImage（用 URL 缓存），原地换图，不挪光标
                    proxy = ["img", dict(d)]
                    photo = self._img_photo(proxy)
                    if photo is not None:
                        self.text.image_configure(name, image=photo)
                        self._imgs_get()[name] = {"photo": photo, "block": proxy}
            except Exception:
                pass
        imgstore.remove_local(old_src)
        store().save(self.data)

    def _fence_line_break(self, ln, lang=""):
        """整行只有 ``` 加语言名时按回车：删掉这行原文，就地生成代码块。

        和「打完围栏敲空格」走同一个落点（insert_code），差别只在「什么时候
        算输入完成」：空格是边打边确认，回车是写完整行的确认。
        """
        self.text.edit_separator()
        self.text.delete(ln + ".0", ln + ".end")
        self.text.mark_set("insert", ln + ".0")
        self.insert_code(lang=lang)
        self.text.edit_separator()
        self.mark_modified()
        return "break"

    # ---------- 引用块 / 行内代码 / 分割线 / 链接 ----------

    def toggle_quote(self):
        """引用块：当前行在「引用 / 正文」之间切换。

        引用是行级样式，和标题互斥（一行不可能既是引用又是标题）。
        回车续接引用；想退出就把当前引用行的字删光、再回一次车
        （空引用行回车 = 跳出引用块）。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        ln = self.text.index("insert").split(".")[0]
        if self._line_has_tag("quote", ln):
            self._set_line_tag(ln, "body")
        else:
            self._set_line_tag(ln, "quote")
        self.mark_modified()

    def toggle_icode(self):
        """行内代码：给选中的文字套上/去掉等宽底色。

        没选区时提示而不是插入一对反引号 —— 编辑器里不显示 markdown 源码，
        留下一对 `` 在正文里反而要用户自己回头删。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("行内代码", "请先选中要变成行内代码的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        if self.text.tag_nextrange("icode", a, b):
            self.text.tag_remove("icode", a, b)
        else:
            self.text.tag_add("icode", a, b)
        self.mark_modified()

    def insert_hr(self):
        """在光标所在行上方插入一条分割线（对应 markdown 的 ---）。

        先在这一行行首补一个换行把它整体下推，腾出的一整行放分割线：
        window 必须独占一行，否则正文会贴在分割线的右边。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        ln = self.text.index("insert").split(".")[0]
        self.text.edit_separator()
        self.text.insert(ln + ".0", "\n")
        self._add_hr(ln + ".0")
        self.text.edit_separator()
        self.mark_modified()

    # ---------- 整行操作：Ctrl+D 复制 / Ctrl+Y 删除 ----------

    def _touched_lines(self):
        """当前要操作的那些行：没选区是光标所在行，有选区是覆盖到的每一行。

        选区终点落在下一行第 0 列时 Tk 会把那一行也算进来（拖到最底下再松手
        就是这种形态），但用户选的其实是「到上一行为止」—— 不去掉它会多删一行。
        """
        t = self.text
        try:
            rng = t.tag_ranges("sel")
            if rng:
                a, b = str(rng[0]), str(rng[-1])
            else:
                a = b = t.index("insert")
        except Exception:
            return []
        try:
            l0 = int(a.split(".")[0])
            l1 = int(b.split(".")[0])
        except Exception:
            return []
        if l1 > l0 and b.split(".")[1] == "0":
            l1 -= 1
        return list(range(l0, l1 + 1))

    def _line_segments(self, ln):
        """把一整行拆成 [(文本, 标签元组)]，并汇报这行有没有嵌入窗口。

        换行符不带标签（行级标签刻意只到换行符为止，否则 title 的行距会把标题
        和下一行之间撑出一道空当），所以只需逐段抄这几段就够了 —— 抄出来的行
        连粗体、引用、标题、待办状态一起抄走。
        """
        t = self.text
        segs, active, has_win = [], [], False
        for key, value, index in t.dump("%d.0" % ln, "%d.end" % ln,
                                        text=True, tag=True, window=True):
            value = str(value)
            if key == "tagon":
                if value not in ("sel", "done") and not self._is_ephemeral_tag(value):
                    if value not in active:
                        active.append(value)
            elif key == "tagoff":
                if value in active:
                    active.remove(value)
            elif key == "window":
                has_win = True
            elif key == "text":
                segs.append((value, tuple(active)))
        return segs, has_win

    def duplicate_line(self):
        """Ctrl+D：把光标所在行整行复制一份插到下面（有选区则整块复制一份）。

        复制整个「块」而不是逐行插（1,2,3 → 1,2,3,1',2',3'），这样连续几行
        复制下来还是挨在一起的一块。
        """
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        lines = self._touched_lines()
        if not lines:
            return
        rows = []
        for ln in lines:
            segs, has_win = self._line_segments(ln)
            # 代码块 / 分割线是嵌入窗口，整行复制复制不了那个窗口 —— 跳过这行，
            # 比插一个空壳要有用（空代码块落在正文里还得用户自己删）。
            if has_win:
                continue
            rows.append(segs)
        if not rows:
            return
        self.text.config(state=tk.NORMAL)
        last = max(lines)
        self.text.edit_separator()
        self.text.insert("%d.end" % last, "\n")
        # 往 insert 标记处连续插 = 顺次往后写（Tk 会把光标推到新文字之后）
        self.text.mark_set("insert", "%d.0" % (last + 1))
        for i, segs in enumerate(rows):
            if i:
                self.text.insert("insert", "\n")
            for txt, tags in segs:
                if txt:
                    self.text.insert("insert", txt, tags)
        # 复制的是一段有序列表时，副本的序号会和原块重号：整块重编成连续的
        # 1..n（P1 自动重编号）。
        self._renumber_numbered_block(last + 1, sep=False)
        self.text.edit_separator()
        self._after_line_op()

    def delete_line(self):
        """Ctrl+Y：删掉光标所在行（有选区则删掉覆盖到的每一行）。"""
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        lines = self._touched_lines()
        if not lines:
            return
        # 记下被删行的缩进：删除后同一行号会装上原来的下一行，可能已经是
        # 更深的子项。重编号要回到「被删掉的那一层」，不能跟着子项走。
        indent_hint = self._indent_len_at(min(lines))
        self.text.config(state=tk.NORMAL)
        self.text.edit_separator()
        for ln in sorted(lines, reverse=True):
            nxt = "%d.0" % (ln + 1)
            tail = self.text.index("end-1c")
            if self.text.compare(nxt, ">", tail):
                # 最后一行没有「下一行的起点」可以删：改吃它前面的那个换行符；
                # 整篇只剩一行时只剩清空.content 这一条路。
                if ln > 1:
                    self.text.delete("%d.end" % (ln - 1), "%d.end" % ln)
                else:
                    self.text.delete("1.0", "1.end")
            else:
                self.text.delete("%d.0" % ln, nxt)
        # 删掉列表中间一项后，后面几项要顶上来重新编号（1,2,3 删掉 2 → 1,2）。
        # 从被删位置所在的行开始找：它现在装着原来的下一行；删的是最后一行时
        # 退到新的末行，让上一个列表块也能收尾。
        # 放在收口分隔线**之前**，删除与重编号同属一次撤销。
        try:
            anchor = min(lines)
            last = int(self.text.index("end-1c").split(".")[0])
            target = self._nearest_numbered_at_indent(min(anchor, last),
                                                      indent_hint)
            if target is not None:
                self._renumber_numbered_block(target, sep=False)
        except Exception:
            pass
        self.text.edit_separator()
        # 被删的行里可能带着代码块 / 分割线窗口。Tk 会把窗口从正文里摘掉，
        # 记账列表（宽度同步用）里的那几条要跟着去掉，否则每次拉宽窗口都在
        # 给一个已经不在正文里的控件设宽度。
        for attr in ("_codes", "_hrs"):
            old = getattr(self, attr, None) or []
            setattr(self, attr, [w for w in old
                                 if getattr(w, "winfo_exists", lambda: False)()])
        self._after_line_op()

    def _after_line_op(self):
        """整行插/删之后收尾：重算列表缩进 + 按惯例触发保存与列表刷新。"""
        # li_<行号> 是按行号派生的动态标签：行号一变原来那批就全错位了
        self._sync_list_indent()
        self.mark_modified()

    # ---------- 超链接 ----------
    # 数据结构：正文里只留**显示文字**，每个链接一个标签 lk_N，URL 存在
    # note["links"] = {"lk_1": "https://…"} 里。
    #
    # 为什么不保留 `[文字](url)` 再用 elide 把后半段藏起来：隐藏区里的字符仍然
    # 参与光标移动、删除和粘贴，用户按几下方向键就会跳到看不见的括号中间，
    # 一删就把语法删坏了。标签跟着字符走，编辑行为和普通文字完全一致。

    def _note_links(self, n=None, create=False):
        n = n or self.current_note()
        if not n:
            return {}
        links = n.get("links")
        if not isinstance(links, dict):
            if not create:
                return {}
            links = {}
            n["links"] = links
        return links

    def _note_key(self, nid):
        return (getattr(self, "_keys", None) or {}).get(nid)

    def _link_tag_at(self, index):
        """某个下标上挂着的链接标签（没有则 None）。"""
        try:
            for t in self.text.tag_names(index):
                if t.startswith(LINK_TAG_PREFIX):
                    return t
        except Exception:
            pass
        return None

    def _new_link_tag(self):
        links = self._note_links(create=True)
        i = 1
        while True:
            t = "%s%d" % (LINK_TAG_PREFIX, i)
            # 既不能和已登记的 URL 撞，也不能和正文里残留的孤儿标签撞
            if t not in links and not self.text.tag_ranges(t):
                return t
            i += 1

    @staticmethod
    def _normalize_url(url):
        """补全协议头：用户常直接粘贴 example.com/a 这种。"""
        url = (url or "").strip()
        if not url:
            return ""
        if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", url) or url.startswith("//"):
            return url
        return "https://" + url

    def _style_link_tag(self, tag):
        """链接标签的样式：固定蓝 + 下划线（与裸网址共用一套观感）。

        用 pal["link"] 而不是 pal["accent"]：accent 是皮肤强调色（多为绿），
        文字变绿就分不清「链接」和「强调」，用户也明确要求蓝色。
        """
        try:
            color = self.pal.get("link") or self.pal["accent"]
            self.text.tag_configure(tag, foreground=color, underline=True)
            self.text.tag_raise(tag)
            # "sel" 是 Text 自带标签、天生排在最底；这里把链接 raise 上去，
            # 选区就跟着沉到链接下面了 —— 选中一段带链接的文字会看不出选中。
            # 谁 raise 谁负责把选区重新顶回去。
            self.text.tag_raise("sel")
        except Exception:
            pass

    def _apply_link(self, a, b, url, tag=None):
        """给 [a, b) 这段文字套上链接，并记下 URL。

        范围**不得跨行**：跨行的选区只取第一行——换行符进了链接范围后，
        回车继承会把新行也整个带成链接，视觉和交互都乱。
        """
        a = self.text.index(a)
        b = self.text.index(b)
        if str(a).split(".")[0] != str(b).split(".")[0]:
            b = self.text.index(a + " lineend")
        if self.text.compare(a, ">=", b):
            return None
        url = self._normalize_url(url)
        if not url:
            return None
        tag = tag or self._new_link_tag()
        self._note_links(create=True)[tag] = url
        self._style_link_tag(tag)
        self.text.tag_add(tag, a, b)
        # "link" 是共用的可点标签：点击打开、悬停变手型都绑在它上面，
        # 裸网址和富链接因此共用一套事件，不用各写一份
        self.text.tag_add("link", a, b)
        return tag

    def _unlink(self, tag, a=None, b=None):
        """取消一个链接：摘掉标签、删掉登记的 URL，文字原样留下。"""
        n = self.current_note()
        if a is None or b is None:
            r = self.text.tag_ranges(tag)
            a, b = (r[0], r[1]) if r else (None, None)
        if a is not None and b is not None:
            self.text.tag_remove(tag, a, b)
            self.text.tag_remove("link", a, b)
        if n:
            (n.get("links") or {}).pop(tag, None)
        if self._link_tip_tag == tag:
            self._hide_link_tip()

    def _prune_links(self, n):
        """把「文字已经被删光」的链接登记清掉，避免旧地址在 id 复用时复活。"""
        links = n.get("links")
        if not isinstance(links, dict) or not links:
            return
        for t in list(links):
            try:
                if not self.text.tag_ranges(t):
                    links.pop(t, None)
            except Exception:
                pass

    def toggle_link(self):
        """超链接按钮 / Ctrl+K。

        四种情形：
          选中已被链接的文字 → 取消链接（再按一次就是"撤销"）
          选中一段网址       → 直接建成链接，显示文字仍是网址本身
          选中普通文字       → 弹窗填地址，文字保持不变
          没有选区           → 光标在链接里就编辑它，否则新建（文字 + 地址都手填）
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        sel = self.text.tag_ranges("sel")
        if sel:
            a, b = sel[0], sel[1]
            tag = self._link_tag_at(a)
            if tag:
                self._unlink(tag, a, b)
                self.mark_modified()
                return
            txt = self.text.get(a, b).strip()
            if re.match(r"^https?://\S+$", txt):
                self._apply_link(a, b, txt)
                self.mark_modified()
                return
            res = self._ask_link(text=txt, url="", title="插入超链接")
            if not res:
                return
            new_text, url = res["text"], res["url"]
            if new_text and new_text != self.text.get(a, b):
                self.text.delete(a, b)
                self.text.insert(a, new_text)
                b = self.text.index("%s +%dc" % (a, len(new_text)))
            self._apply_link(a, b, url)
            self.mark_modified()
            return
        tag = self._link_tag_at("insert")
        if tag:
            r = self.text.tag_ranges(tag)
            res = self._ask_link(text=self.text.get(r[0], r[1]) if r else "",
                                 url=self._note_links().get(tag, ""),
                                 title="编辑超链接", removable=True)
            if not res:
                return
            if res["text"] and r and res["text"] != self.text.get(r[0], r[1]):
                self.text.delete(r[0], r[1])
                self.text.insert(r[0], res["text"])
                r = (self.text.index(r[0]),
                     self.text.index("%s +%dc" % (r[0], len(res["text"]))))
            if res["url"]:
                if r:
                    self._apply_link(r[0], r[1], res["url"], tag=tag)
            elif r:
                self._unlink(tag, r[0], r[1])
            self.mark_modified()
            return
        res = self._ask_link(text="", url="", title="插入超链接")
        if not res:
            return
        label = res["text"] or res["url"]
        url = res["url"]
        if not label or not url:
            return
        # 插入**前**先取起点：插入之后 insert 标记已经移到文字末尾，
        # 再拿它当范围起点会把标签套到插入点后面的文字上（用户看到的
        # 就是「后面的字莫名变蓝、删掉链接字也删不掉」）。
        a = self.text.index("insert")
        self.text.insert(a, label)
        b = self.text.index("%s +%dc" % (a, len(label)))
        self._apply_link(a, b, url)
        self.mark_modified()

    def _convert_md_links(self, ln=None):
        """把行尾刚敲完的 `[文字](url)` 收成「蓝色带下划线的文字」。

        只认**紧贴光标之前**的那一个（允许后面跟一个刚敲出来的空格）：
        否则正文里正常写着玩的 [x](y) 会在这一行任意敲空格时被改掉。
        """
        try:
            ln = ln or self.text.index("insert").split(".")[0]
            col = int(str(self.text.index("insert")).split(".")[1])
            line = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return False
        if "](" not in line or self._window_at_line_start(ln):
            return False
        head = line[:col].rstrip(" ")
        hit = None
        for m in MD_LINK_RE.finditer(head):
            if m.end() == len(head):        # 最后收尾的那一个才算「刚敲完」
                hit = m
        if hit is None:
            return False
        label, url = hit.group(1), hit.group(2)
        a = "%s.%d" % (ln, hit.start())
        b = "%s.%d" % (ln, hit.end())
        whole = hit.end() - hit.start()
        self.text.edit_separator()
        try:
            self.text.delete(a, b)
            self.text.insert(a, label)
            end = self.text.index("%s +%dc" % (a, len(label)))
            self._apply_link(a, end, url)
            # 光标原来在 col（可能还带着刚敲的空格），减去被吃掉的语法长度
            self.text.mark_set("insert", "%s.%d" % (ln, max(0, col - whole + len(label))))
        except Exception:
            return False
        finally:
            self.text.edit_separator()
        self.mark_modified()
        return True

    def _mark_line_links(self, ln=None):
        """把某一行的裸网址标成链接（只加标签，不动文字）。

        先清掉这一行已有的 link，再按当前内容重标：否则把网址改短之后，
        旧的那截还会留着可点击的下划线。
        """
        try:
            ln = ln or self.text.index("insert").split(".")[0]
        except Exception:
            return
        if self._window_at_line_start(ln):
            # 行首是代码块 / 分割线时，字符串下标和 Text 的列号对不上，跳过更安全
            return
        try:
            line = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return
        ls, le = ln + ".0", ln + ".end"
        self.text.tag_remove("link", ls, le)
        if "http" in line:
            for s, e, _url in find_urls(line):
                try:
                    self.text.tag_add("link", "%s.%d" % (ln, s), "%s.%d" % (ln, e))
                except Exception:
                    pass
        # 上面把整行的 link 清空了，富链接的样式必须补回来，否则它们会掉成黑字
        for tag in self._note_links():
            r = self.text.tag_ranges(tag)
            for i in range(0, len(r), 2):
                try:
                    self.text.tag_add("link", r[i], r[i + 1])
                except Exception:
                    pass

    def _link_url_at(self, index):
        """某个下标对应的真实 URL（富链接查登记表，裸网址就是它自己）。"""
        tag = self._link_tag_at(index)
        if tag:
            return self._note_links().get(tag, "")
        return ""

    def _on_link_click(self, event):
        """点链接：内链（note:<id>）跳到那篇笔记，网址用默认浏览器打开。

        Tk 里没有内置浏览器，外链只能交给系统；内链则是应用内导航。
        """
        # tag 绑定先于 widget 绑定执行：Alt+拖动从链接文字上起手时，这一条会抢在
        # _on_text_press 前面跑，让路给矩形选择，别顺手把链接打开了。
        if self._alt_down(event):
            return None
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
        except Exception:
            return None
        url = self._link_url_at(idx)
        if not url:
            # 裸网址：文字本身就是地址
            try:
                ranges = self.text.tag_ranges("link")
            except Exception:
                return None
            for i in range(0, len(ranges), 2):
                a, b = ranges[i], ranges[i + 1]
                if self.text.compare(a, "<=", idx) and self.text.compare(idx, "<", b):
                    txt = self.text.get(a, b).strip()
                    if txt.startswith("http"):
                        url = txt
                    break
        if not url:
            return None
        nid = note_id_of_url(url)
        if nid:
            self._goto_note(nid)
            return "break"
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            self._show_info("链接", "打不开这个网址：\n\n%s" % url)
        return "break"

    # ---------- 悬停显示链接地址（可就地编辑） ----------

    def _bare_link_span_at(self, idx):
        """指针落在「裸网址」上时返回它的 (起, 止)，否则 None。

        裸网址只有共用的 "link" 标签、没有 lk_N —— 显示文字就是网址本身。
        富链接那段同时也挂着 "link"，但调用方已先查过 lk_N，不会重复命中。
        """
        try:
            ranges = self.text.tag_ranges("link")
        except Exception:
            return None
        for i in range(0, len(ranges), 2):
            a, b = ranges[i], ranges[i + 1]
            try:
                if self.text.compare(a, "<=", idx) and self.text.compare(idx, "<", b):
                    if self.text.get(a, b).strip().startswith("http"):
                        return (str(a), str(b))
                    return None
            except Exception:
                return None
        return None

    def _on_text_motion(self, event):
        """指针在正文里移动：停在链接上就准备弹出编辑小窗。"""
        if not self.current_note():
            return
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
        except Exception:
            return
        self._cancel_link_tip_show()
        tag = self._link_tag_at(idx)
        span = None if tag else self._bare_link_span_at(idx)
        if not tag and not span:
            self._link_tip_tag = None
            self._link_tip_span = None
            # 指针从链接移向下方的小窗必然经过非链接正文，隐藏延时必须
            # 长过这段移动（否则小窗在半路消失 =「悬停出来了却编辑不了」）
            self._schedule_link_tip_hide(600)
            return
        self._cancel_link_tip_hide()
        if self._link_tip_tag == tag and self._link_tip_span == span \
                and self._link_editor is not None \
                and self._link_editor.win is not None:
            return
        self._link_tip_tag = tag
        self._link_tip_span = span
        self._link_tip_show_job = self._after(
            320, lambda t=tag, x=event.x_root, y=event.y_root:
            self._show_link_tip(t, x, y))

    def _cancel_link_tip_show(self):
        """撤掉还没到点的「弹小窗」计时（指针每动一下都会重排，不撤会堆积）。"""
        job = getattr(self, "_link_tip_show_job", None)
        if job is not None:
            self._cancel_after(job)
            self._link_tip_show_job = None

    def _show_link_tip(self, tag, x, y):
        """弹出小窗：显示文字 + 地址，两项都能改。

        tag 为 None 表示悬停的是裸网址 —— 它只有共用的 "link" 标签，
        显示文字就是那串网址本身（此时范围记在 _link_tip_span 里）。
        """
        if self._link_tip_tag != tag or self.is_readonly():
            return
        span = getattr(self, "_link_tip_span", None)
        if tag:
            url = self._note_links().get(tag, "")
            r = self.text.tag_ranges(tag)
            if not url or not r:
                return
            label = self.text.get(r[0], r[1])
        else:
            if not span:
                return
            label = self.text.get(span[0], span[1])
            url = label.strip()
            if not url.startswith("http"):
                return
        if self._link_editor is None:
            from widgets import LinkEditor
            self._link_editor = LinkEditor(self.root, self.pal, self._commit_link_edit,
                                           on_enter=self._cancel_link_tip_hide)
        self._link_editor.show(x, y + 18, url, tag=tag, text=label)

    def _commit_link_edit(self, tag, new_text, new_url):
        """小窗里改完：地址空 = 取消链接；显示文字改了就替换正文里那一段。

        tag 为 None 表示这一段原本是裸网址（文字即地址）。只要动了一项就把它
        升级成富链接（文字与地址分开存）；一项都没动就什么都不做 —— 免得每次
        悬停都把好好的裸网址改写一遍。
        显示文字留空视为「不改文字」，只有清空地址才是取消链接。
        """
        n = self.current_note()
        if not n or self.is_readonly():
            return
        span = getattr(self, "_link_tip_span", None)
        if tag is None:
            if not span:
                return
        elif tag not in (n.get("links") or {}):
            return
        new_url = self._normalize_url(new_url)
        try:
            if tag is None:
                old = self.text.get(span[0], span[1])
            else:
                r = self.text.tag_ranges(tag)
                if not r:
                    return
                old = self.text.get(r[0], r[1])
                span = (str(r[0]), str(r[1]))
        except Exception:
            return
        if not new_url:
            if tag is None:
                return                  # 裸网址：地址就是文字，清空没有意义
            self._unlink(tag)
            self.mark_modified()
            return
        want = (new_text or "").replace("\r", " ").replace("\n", " ").strip()
        if tag is None and want == old and self._normalize_url(old) == new_url:
            return                      # 一个字没动
        if tag is not None and not want:
            want = old                  # 只改地址
        self.text.edit_separator()
        try:
            if want != old:
                a = span[0]
                self.text.delete(a, span[1])
                self.text.insert(a, want)
                span = (a, str(self.text.index("%s +%dc" % (a, len(want)))))
            if tag is None:
                # 裸网址 → 富链接：文字和地址从此分开存
                self._apply_link(span[0], span[1], new_url)
            else:
                # 上面那段 delete 已经把标签摘掉了，按新长度重新套回同一个 tag
                # （换 tag 的话正文范围和 note["links"] 里的登记就对不上了）
                self.text.tag_add(tag, span[0], span[1])
                self.text.tag_add("link", span[0], span[1])
                n["links"][tag] = new_url
        finally:
            self.text.edit_separator()
        self.mark_modified()

    def _bind_link_tip(self):
        """把「悬停看地址」挂到正文上（一次即可，换肤/换笔记都不用重绑）。"""
        self.text.bind("<Motion>", self._on_text_motion, add="+")
        self.text.bind("<Leave>", lambda e: self._schedule_link_tip_hide(260), add="+")
        self.text.bind("<Button-1>", lambda e: self._hide_link_tip(), add="+")
        self.text.bind("<Key>", lambda e: self._hide_link_tip(), add="+")
        self.text.bind("<MouseWheel>", lambda e: self._hide_link_tip(), add="+")
        # 「任务提醒列表」被截断的格子：指针压上去浮出全文。同一个 <Motion> 上
        # 两条绑定互不干扰（上面那条只管链接），收起的时机也一样
        self.text.bind("<Motion>", self._on_cell_motion, add="+")
        self.text.bind("<Leave>", lambda e: self._hide_cell_tip(), add="+")
        self.text.bind("<Button-1>", lambda e: self._hide_cell_tip(), add="+")
        self.text.bind("<Key>", lambda e: self._hide_cell_tip(), add="+")
        self.text.bind("<MouseWheel>", lambda e: self._hide_cell_tip(), add="+")

    # ---------- 悬停看「任务提醒列表」里被截断的格子 ----------
    # 四列固定宽度（store.ALARM_COLS），超宽的格子在正文里只剩前半截 + …；
    # 原样文字由 alarm_index_content 收在 tips 里（app.refresh 时缓存到
    # _alarm_cell_tips），这里只负责「指针压在哪一格 → 把那一格的全文字浮出来」。

    def _cell_tag_at(self, idx):
        """idx 落在一个被截断的格子上时返回它的标签名，否则 None。"""
        try:
            for name in self.text.tag_names(idx):
                if name.startswith(ALARM_CELL_TAG):
                    return name
        except Exception:
            pass
        return None

    def _on_cell_motion(self, event):
        """指针在正文里移动：停在汇总表被截断的格子上就把全文浮出来。

        汇总表是只读的系统报表，这一层只做展示 —— 不给链接那种「就地编辑」
        小窗，免得让人以为报表里的格子能改。
        """
        if event is None:
            return
        n = self.current_note()
        if not n or n.get("id") != ALARM_INDEX_ID:
            if self._cell_tip_tag is not None:
                self._hide_cell_tip()
            return
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
        except Exception:
            return
        tag = self._cell_tag_at(idx)
        # 窗还在就不再重建：<Motion> 每一像素都会来一次，反复 destroy+create
        # 会让提示一直闪。窗被收掉（滚轮 / 离开）后回到同一格要能重新浮出，
        # 所以判据是「同一格 **且** 窗还在」
        if tag == self._cell_tip_tag and self._cell_tip_win is not None:
            return
        self._cell_tip_tag = tag
        if not tag:
            self._hide_cell_tip()
            return
        full = (getattr(self, "_alarm_cell_tips", None) or {}).get(tag)
        if not full:
            self._hide_cell_tip()       # 标签在、全文没了（刷新过一轮）就当没截断
            return
        self._cell_tip_show(event.x_root + dpi.px(14), event.y_root + dpi.px(18),
                            full)

    def _cell_tip_show(self, x, y, text):
        p = self.pal
        self._drop_cell_tip_win()      # 只收窗，**别动 _cell_tip_tag**：它被清掉的话
        #                                下一次 <Motion> 会当成「换了一格」重建窗口，肉眼就是闪
        try:
            win = tk.Toplevel(self.root)
            win.wm_overrideredirect(True)
            # 跟闹钟提示同一套：主窗临时置顶时，浮窗不跟着置顶就会被自家窗口盖住
            win.attributes("-topmost", True)
            win.configure(bg=_popover_border(p))
            # wraplength 按像素给：任务名可以很长，一行铺满屏幕还不如折成几行
            lbl = tk.Label(win, text=text, bg=_popover_bg(p),
                           fg=p.get("text", "#222222"), font=(FONT[0], 10),
                           bd=0, justify="left", anchor="w",
                           wraplength=dpi.px(420),
                           padx=dpi.px(10), pady=dpi.px(6))
            lbl.pack(padx=dpi.px(1), pady=dpi.px(1))
            win.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            # 夹进**鼠标所在那块屏**的工作区（多屏时 winfo_screenwidth 只报主屏）
            area = win_frame.work_area_at(x, y, fallback=win)
            px, py = win_frame.clamp_pos(area, x, y, w, h, pad=dpi.px(4))
            win.geometry("+%d+%d" % (px, py))
            self._cell_tip_win = win
        except Exception:
            self._cell_tip_win = None

    def _drop_cell_tip_win(self):
        """只销毁浮窗，保留「指针压着哪一格」的记忆。"""
        win = getattr(self, "_cell_tip_win", None)
        self._cell_tip_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _hide_cell_tip(self):
        """收起浮窗，并把「指针压着哪一格」一并清掉。

        ⚠️ 和 _alarm_tip_hide 不同，这里必须清 _cell_tip_tag：它唯一的用途是
        去重，清了下次移动才会重新判断；而闹钟那边的 _alarm_hover 还要靠
        它判断「是否还在同一枚上」，两者语义不同别照抄。
        """
        self._cell_tip_tag = None
        self._drop_cell_tip_win()

    def _schedule_link_tip_hide(self, ms=200):
        self._cancel_link_tip_hide()
        self._link_tip_job = self._after(ms, self._hide_link_tip)

    def _cancel_link_tip_hide(self):
        if self._link_tip_job is not None:
            self._cancel_after(self._link_tip_job)
            self._link_tip_job = None

    def _hide_link_tip(self):
        self._cancel_link_tip_hide()
        self._cancel_link_tip_show()
        self._link_tip_tag = None
        self._link_tip_span = None
        if self._link_editor is not None:
            self._link_editor.hide()

    def _restyle_links(self):
        """给正文里所有链接标签刷一遍配色（换肤 / 载入笔记后调用）。"""
        try:
            for t in self.text.tag_names():
                if t.startswith(LINK_TAG_PREFIX):
                    self._style_link_tag(t)
        except Exception:
            pass

    # ---------- 时间函数 ----------

    def _expand_time_token(self, ln=None):
        """`date()` / `datetime()` 敲完空格或回车 → 换成当前时间。

        与 markdown 前缀一样只在「敲完那一刻」展开：位置必须紧贴光标之前。
        写成函数名形式（而不是 {{date}} 之类的占位符）是因为它看起来就像代码，
        在笔记里不容易和正文混淆。
        """
        if not self.current_note() or self.is_readonly():
            return False
        try:
            ln = ln or self.text.index("insert").split(".")[0]
            col = int(str(self.text.index("insert")).split(".")[1])
            line = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return False
        if self._window_at_line_start(ln):
            return False
        hit = find_time_call(line[:col].rstrip(" "))
        if not hit:
            return False
        name, start = hit
        now = datetime.datetime.now()
        val = (now.strftime("%Y-%m-%d %H:%M:%S") if name == "datetime"
               else now.strftime("%Y-%m-%d"))
        tok = len(name) + 2                     # 名字 + "()"
        a, b = "%s.%d" % (ln, start), "%s.%d" % (ln, start + tok)
        self.text.edit_separator()
        try:
            self.text.delete(a, b)
            self.text.insert(a, val)
            self.text.mark_set("insert", "%s.%d" % (ln, max(0, col - tok + len(val))))
        except Exception:
            return False
        finally:
            self.text.edit_separator()
        self.mark_modified()
        return True

    # ---------- $ 公式 ----------

    def _refit_row_tags(self, ln):
        """把本行已经有的行级标签重新铺满整行。

        Tk 在 tag 范围的**终点**处插入字符不会扩展范围，而补结果正好插在
        行尾 —— 第一行（标题）、`##` 行、引用行里算公式时，行尾那几个字会
        掉成正文字号。删 `$` 那一步还会让范围起点错开。所以编辑完统一补一遍。

        只处理「本来就覆盖到这一行」的标签：`tag_add` 对不含本行的标签会
        凭空造出一段覆盖，标题色跑到别行去。空行那个 0 长度范围的坑不用管
        —— 空行里写不出 `$公式=`。
        """
        ls, le = "%d.0" % ln, "%d.end" % ln
        for t in self.LINE_TAGS:
            r = self.text.tag_ranges(t)
            for k in range(0, len(r), 2):
                # 区间与 [ls, le) 有交叠才算「本行已有」
                if (self.text.compare(r[k], "<", le)
                        and self.text.compare(r[k + 1], ">", ls)):
                    self.text.tag_add(t, ls, le)
                    break

    def _expand_formula(self, idx=None):
        """`$5+6=` 敲完空格 → 补成 `5+6= 11`（`$` 是触发键，算完即删）。

        `idx` 是**空格按下那一刻**的光标位置（在空格插进正文之前登记的），
        不是执行时的 insert —— idle 真正跑起来时用户可能已经又敲了字。位置
        对不上（`=` 与确认空格不在原处、或光标已经不在空格之后）就什么都不做，
        免得把结果塞进别的地方。

        算不出来（没有 `$` 起头、语法不认识、除零、tan90）也什么都不做：
        静默是本功能的默认姿态，弹框只会打断打字。

        两处编辑**从右往左**做（先插入结果，再删 `$`）：`$` 之后的所有列号
        都要左移一格，反过来做就得手算偏移，多一个出错的机会。

        撤销要一次到位，所以这两步之间**关了自动分隔符**：Tk 默认在「插入」
        与「删除」类型切换时自动打分隔符，不关的话一次 Ctrl+Z 只会撤回半件事
        （要么只还回 `$`、要么只撤掉结果），用户得按两次。
        """
        if not idx or not self.current_note() or self.is_readonly():
            return False
        try:
            ln, col = str(idx).split(".")
            ln, col = int(ln), int(col)
            line = self.text.get("%d.0" % ln, "%d.end" % ln)
            cur = str(self.text.index("insert"))
        except Exception:
            return False
        if cur != "%d.%d" % (ln, col + 1):
            return False                 # 光标已经走开（又敲了字 / 点了别处）
        if self._window_at_line_start(str(ln)):
            return False                 # 行首是嵌入的代码块 / 分割线，不掺和
        hit = calc.expand(line, col)
        if not hit:
            return False
        val, at, mark = hit
        self.text.edit_separator()
        auto = self.text.cget("autoseparators")   # 记下原值，别把全局状态写死
        try:
            self.text.configure(autoseparators=False)
            self.text.insert("%d.%d" % (ln, at), val)
            self.text.delete("%d.%d" % (ln, mark), "%d.%d" % (ln, mark + 1))
            self._refit_row_tags(ln)              # 新插的字要跟上行级样式
            self.text.mark_set("insert", "%d.%d" % (ln, at - 1 + len(val)))
        except Exception:
            return False
        finally:
            self.text.configure(autoseparators=auto)
            self.text.edit_separator()
        self.mark_modified()
        return True

    # ---------- 任务提醒（正文里的卡片） ----------
    # 2026-11-18 改版：正文从「一枚 16px 小闹钟」升级成 AlarmCard 卡片。
    #   self._alarms —— "alarm:<id>" → {"id","text","at"}，当前笔记打开时有效
    #   self._alarm_cards —— 同键 → AlarmCard 实例（窗口控件），换肤/状态翻转就地重画
    #   content          —— ["alarm", {...}]，落库的唯一形态（见 serialize）
    # 卡片是 text.window_create 嵌的窗口，跟代码块/分割线同一条机制：不占字符、
    # 整块删除，序列化从 dump(window=True) 认领；「响过没有」仍记在
    # settings["alarm_fired"]（理由见 alarm.py 文件头），确认态记在 alarm_acked。

    ALARM_TICK_MS = 20000        # 轮询间隔：20 秒。再密没有意义（提醒的容差是分钟级）

    def _alarms_get(self):
        """当前笔记的闹钟表：name → 数据。切笔记时整体换掉。"""
        m = getattr(self, "_alarms", None)
        if not isinstance(m, dict):
            m = self._alarms = {}
        return m

    def _alarm_cards_get(self):
        """当前笔记的卡片表：name → AlarmCard；与 _alarms 同键。"""
        m = getattr(self, "_alarm_cards", None)
        if not isinstance(m, dict):
            m = self._alarm_cards = {}
        return m

    def _alarm_card_state(self, data):
        """卡片/汇总表共用的四态判定：查 ack 后交给 alarm.state_of。"""
        return alarm.state_of(data, acked=self._alarm_is_acked(data))

    def _alarm_is_acked(self, data):
        return bool(self._alarm_acked_map().get(self._alarm_state_key(data)))

    def _alarm_state_key(self, data):
        """当前周期的 acked/fired 键：once 用旧键，重复提醒带 @日期槽位。"""
        return alarm.state_key(self._alarm_key(data), data)

    def _alarm_add(self, idx, data, move=True):
        """在 idx 处放一张提醒卡片并登记数据；失败返回 False（正文不动）。

        渲染旧笔记时 move=False —— 那时候光标该由载入流程自己摆，不能被这里挪走。
        """
        d = {"id": data.get("id") or alarm.new_id(),
             "text": alarm.normalize_text(data.get("text")),
             "at": float(data.get("at") or 0)}
        # 重复规则整块带上；once 不写额外字段（没有 repeat 键 = 老数据，天然兼容）
        if alarm.repeat_kind(data) != alarm.REPEAT_ONCE:
            d.update(alarm.normalize_rule(alarm.repeat_kind(data),
                                          alarm.hm_of(data), data.get("days")))
        name = "alarm:" + d["id"]
        try:
            src = _note_head(self.current_note() or {}) or "本笔记"
            card = AlarmCard(self.text, self, d, source=src)
            self.text.window_create(idx, window=card, padx=0, pady=dpi.px(3))
            card.set_width(self._code_width())
            # 卡片是块级结构项，必须独占一行：正文中它占位 1 字符，紧随其后若不是
            # 换行就补一个。⚠️ 用 `at +1c`（窗口占位符**之后**）取字符 —— 早期版本
            # 取 `at` 本身，读到的是窗口占位符的空串，判断永远成立、把已有换行也
            # 重复插一遍。这个换行会被 serialize() 当普通文本落库，往返稳定。
            at = self.text.index(card)
            nxt = self.text.get("%s +1c" % at, "%s +1c lineend" % at)
            if nxt.strip("\n") or not nxt:
                self.text.insert("%s +1c" % at, "\n")
                at = self.text.index(card)
            if move:
                self.text.mark_set("insert", "%s +2c" % at)
        except Exception:
            return False
        self._alarms_get()[name] = d
        self._alarm_cards_get()[name] = card
        return True

    def _on_paste_image(self, event=None):
        """剪贴板里是位图时：存 media、嵌进正文，并吞掉默认文本粘贴。

        放在 <<Paste>> 链的**最后**（见 app_ui 的绑定顺序）：前面的 todo 修复
        只记行号、不 return break；这里若发现不是图片就放行，Tk 默认插入照常。
        """
        if not self.current_note() or self.is_readonly():
            return None
        try:
            from PIL import ImageGrab
            im = ImageGrab.grabclipboard()
        except Exception:
            return None
        if im is None or not hasattr(im, "save"):
            return None                 # 剪贴板是文字 / 文件路径，交给默认粘贴
        try:
            import io
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            block = self._img_make_block(buf.getvalue(), alt="粘贴的图片")
        except Exception as e:
            self._show_error("插入图片失败", "剪贴板图片读不出来：\n%s" % e)
            return "break"
        self.text.config(state=tk.NORMAL)
        self.text.edit_separator()
        if self._img_add(tk.INSERT, block, move=True):
            self.text.edit_separator()
            self.mark_modified()
            self._img_after_insert(block)
            return "break"
        self.text.edit_separator()
        return None

    def _img_gc(self, resave=True):
        """清理 data/media/ 下没人引用的图片。

        引用方 = 当前全库 + 回收站快照里的笔记（删了还能还原，图不能先没）。
        图床模式上传成功后已即时删了本地文件，这里只兜底「删笔记 / 空块」留下的。
        `resave=False` 用于落库链路（_flush_save 已存过）避免二次写库。
        """
        notes = list(self.data.get("notes") or [])
        try:
            # 回收站快照也算引用方：单条笔记 / 文件夹子树两种形状由纯函数统一处理
            notes.extend(imgstore.notes_from_recycle(store().recycle_all()))
        except Exception:
            pass
        try:
            removed = imgstore.gc_orphans(notes)
        except Exception:
            return
        if removed and resave:
            save_data(self.data)

    def _imgs_get(self):
        """当前笔记的图片对象表：name → PhotoImage。切笔记时整体换掉。"""
        m = getattr(self, "_imgs", None)
        if not isinstance(m, dict):
            m = self._imgs = {}
        return m

    def _img_photo(self, block):
        """img 数据块 → PhotoImage。失败返回 None（正文里就不放图，不留空位）。

        ⚠️ PhotoImage 必须挂在实例上保活，和闹钟同一个坑：对象被 GC 后 Tk 里
        那块位置变空白且不报错，要等下一次重绘才显形。

        ⚠️ 这里**只读本地 / 缓存，绝不联网**。以前图床 URL 是就地同步下载的，
        打开一条带远程图的笔记＝主线程上串行下 N 张图，网络一慢整窗假死；
        https 被掐时还要白等 20 秒超时。取不到的交给 _img_add 排后台任务。
        """
        try:
            d = block[1] if len(block) > 1 and isinstance(block[1], dict) else {}
        except Exception:
            return None
        src = str(d.get("src") or "")
        if not src:
            return None
        path = imgstore.src_path(src)
        if path is None and imgstore.is_remote(src):
            path = self._img_cache_read(src)
        if not path or not os.path.isfile(path):
            return None
        try:
            return tk.PhotoImage(file=path)
        except tk.TclError:
            return None

    def _img_cache_path(self, url):
        """图床 URL → 本地缓存文件路径（按 URL 的 hash 命名，同一地址只存一份）。"""
        import hashlib
        import store as _store
        cache = os.path.join(_store.BASE, imgstore.MEDIA_DIR_NAME, "cache")
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            return None
        return os.path.join(cache,
                            hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + ".png")

    def _img_cache_put(self, url, png):
        """把已经拿到的图浏览器缓存写盘 —— 免得渲染时按 URL 再下一次。"""
        path = self._img_cache_path(url)
        if not path:
            return False
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path)
            return True
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def _img_cache_read(self, url):
        """缓存里**已有**这张图 → 返回路径；没有返回 None。只碰文件，绝不联网。"""
        path = self._img_cache_path(url)
        if path and os.path.isfile(path):
            return path
        return None

    def _img_cache_fetch(self, url):
        """确保缓存里有这张图 → (路径|None, 失败原因|None)。

        ⚠️ 只许在后台线程里调（会联网 + 写盘）。主线程调它就是把它同步下载
        的老问题原样搬回来。下载统一走 imgstore.fetch_bytes（自带 https→http
        降级 +「是不是真图片」的魔数校验）。
        """
        path = self._img_cache_read(url)
        if path:
            return path, None
        path = self._img_cache_path(url)
        if not path:
            return None, "缓存目录不可用"
        try:
            raw = imgstore.fetch_bytes(url)
            png, _w, _h = imgstore.normalize_image(raw)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path)
            return path, None
        except Exception as e:
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass
            return None, (str(e) or type(e).__name__)

    def _img_remote_async(self, url, cb):
        """图床图后台取：缓存命中立刻回调；否则线程里下、回主线程调 cb(path, err)。

        同一 URL 的并发请求合并成一次下载 —— 一条笔记里同一张图可能出现好几次
        （引用同一地址 / 重开后重渲染），没必要发 N 个请求。
        回调里的异常要吞掉：那是别的图元的事，不能让它打断整条回传链。
        """
        cached = self._img_cache_read(url)
        if cached:
            cb(cached, None)
            return
        waiters = getattr(self, "_img_waiters", None)
        if not isinstance(waiters, dict):
            waiters = self._img_waiters = {}
        if url in waiters:
            waiters[url].append(cb)      # 已经在下了：挂上去等同一份结果
            return
        waiters[url] = [cb]

        def work():
            return self._img_cache_fetch(url)

        def done(res):
            path, err = None, None
            if isinstance(res, tuple) and len(res) == 2:
                path, err = res
                if path is False:        # _img_async 的异常回传协议 (False, msg)
                    path, err = None, (err or "下载失败")
            for f in waiters.pop(url, []):
                try:
                    f(path, err)
                except Exception:
                    pass

        self._img_async(work, done)

    def _img_loading_photo(self):
        """远程图未就绪时的灰色占位块（全程序共用一张，尺寸固定）。

        作用只有一个：先占住图元的位置，让 image_configure 能在原位换图；
        没有它就得等下载完再把图插进正文，用户会看到「文字突然消失又出现」。
        """
        ph = getattr(self, "_img_loading_ph", None)
        if ph is None:
            w, h = dpi.px(180), dpi.px(56)
            ph = tk.PhotoImage(width=w, height=h)
            ph.put(self.pal.get("line") or "#dcdce0", to=(0, 0, w, h))
            self._img_loading_ph = ph
        return ph

    def _img_broken_photo(self):
        """下载失败的占位块：灰底 + 一个 ✕（按主题色画，换肤后重建）。"""
        key = (self.pal.get("line"), self.pal.get("soft"))
        ph = getattr(self, "_img_broken_ph", None)
        if ph is not None and getattr(self, "_img_broken_key", None) == key:
            return ph
        w, h = dpi.px(180), dpi.px(56)
        bg = key[0] or "#dcdce0"
        fg = key[1] or "#76767e"
        cx, cy = w // 2, h // 2
        arm = max(4, dpi.px(7))
        grid = [[bg] * w for _ in range(h)]
        # 两笔 2px 粗的对角线拼成 ✕（Canvas 走 GDI 不抗锯齿，整数像素最干净）
        for i in range(-arm, arm + 1):
            for t in (0, 1):
                for (x, y) in ((cx + i, cy + i + t), (cx + i, cy - i + t)):
                    if 0 <= x < w and 0 <= y < h:
                        grid[y][x] = fg
        ph = tk.PhotoImage(width=w, height=h)
        ph.put(" ".join("{" + " ".join(row) + "}" for row in grid))
        self._img_broken_ph, self._img_broken_key = ph, key
        return ph

    def _img_swap(self, name, path, err=None):
        """占位块 → 真图（主线程回调）。下载失败就换成「✕」占位块。"""
        rec = self._imgs_get().get(name)
        if not isinstance(rec, dict):
            return                       # 已切笔记 / 图已删：这枚图元不归我管了
        if not path:
            # ⚠️ **绝不能删这枚图元**：图元是数据块在 serialize() 里的唯一凭据，
            # 删掉它下一次落库就把这块图从正文里抹掉 —— 一次临时断网 = 永久丢图。
            # 换成 ✕ 占位块：图还在、块还在，点一下就是现成的重试入口
            # （图片地址对话框里改回这个 URL，确定即可重新下载）。
            try:
                photo = self._img_broken_photo()
                self.text.image_configure(name, image=photo)
            except tk.TclError:
                return
            rec["photo"] = photo
            return
        try:
            photo = tk.PhotoImage(file=path)
        except tk.TclError:
            return
        try:
            self.text.image_configure(name, image=photo)
        except tk.TclError:
            return
        rec["photo"] = photo             # 保活：换掉的那张可以被回收了

    def _img_add(self, idx, block, move=False):
        """在 idx 处嵌一张图并登记数据；返回是否成功。

        ⚠️ name 用 "img:<id>" 而不是图片路径：serialize 靠 name 前缀认领，
        重开笔记时同一张图的 src 可能已从本地换成图床 URL，name 必须稳定。
        """
        try:
            d = block[1] if len(block) > 1 and isinstance(block[1], dict) else {}
        except Exception:
            return False
        ident = str(d.get("id") or imgstore.img_id())
        name = "img:" + ident
        photo = self._img_photo(block)
        remote = ""
        if photo is None:
            # 远程图还没缓存：先塞灰块占位 + 排后台下载，下载完原地换图。
            # 本地图取不到就是真坏了（文件被删 / 格式 Tk 不认），照旧判失败。
            src = str(d.get("src") or "")
            if not imgstore.is_remote(src):
                return False
            photo = self._img_loading_photo()
            remote = src
        try:
            self.text.image_create(idx, image=photo, name=name, padx=2, pady=2)
            if move:
                self.text.mark_set("insert", "%s +1c" % idx)
        except tk.TclError:
            return False
        # 同时存 block：serialize 靠 name 反查原数据（src/w/h/alt 全在这里）
        self._imgs_get()[name] = {"photo": photo, "block": block}
        if remote:
            self._img_remote_async(remote,
                                   lambda path, err, n=name: self._img_swap(n, path, err))
        return True

    # ---------- 点击图片 → 看 / 改它的 Markdown 源 ----------
    # 为什么是弹窗而不是「把图就地换成一行 ![](url)」：正文里的图是 image_create
    # 图元，换成文字之后 Tk 的 undo 只记字符、**不记图元** —— 用户随手一下 Ctrl+Z，
    # 那枚图就再也回不来；而此刻数据块已经不在 serialize() 里，本地文件会被
    # _img_gc 当成孤儿清掉，等于「撤销一步 = 删图」。弹窗只改数据块的 src，
    # 图元原地 image_configure 换图，撤销 / 落库 / 孤儿回收还是原来那条链。

    # ---------- 图片右键菜单：改地址 / 上传图床 ----------

    def _img_popup(self, e, name):
        """右键某张图：远程图给「复制地址 / 更换地址」，本地图再加「上传到图床」。

        点击已经不再弹窗了（用户定的：悬停看 Markdown 源），改地址、上传都收到
        这里来。只读笔记只给复制类动作；「更换地址」置灰时**把原因写进 label** ——
        一项点不动的菜单，用户会以为程序坏了。
        """
        rec = self._imgs_get().get(name)
        block = rec.get("block") if isinstance(rec, dict) else None
        if not (isinstance(block, list) and len(block) > 1
                and isinstance(block[1], dict)):
            return
        src = str(block[1].get("src") or "")
        local = imgstore.is_local(src)
        ro = self.is_readonly()
        settings = (self.data.get("settings") or {}) if getattr(self, "data", None) else {}
        bed = piclist.configured(settings)

        m = PopupMenu(self.root, self.pal)
        if local:
            if ro:
                up_label, up_ok = "上传到图床（只读笔记）", False
            elif not bed:
                up_label, up_ok = "上传到图床（需先在设置里填 PicList路径）", False
            else:
                up_label, up_ok = "上传到图床", True
            m.add(up_label, icon="export", enabled=up_ok,
                  command=lambda: self._img_upload_local(name))
            m.add("复制本地路径", icon="folder",
                  command=lambda: self._img_copy_src(name, True))
        else:
            m.add("复制图片地址", icon="link",
                  command=lambda: self._img_copy_src(name, False))
        m.add_separator()
        m.add("更换图片地址（只读笔记）" if ro else "更换图片地址…", icon="pencil",
              enabled=not ro, command=lambda: self._img_source_dialog(name))
        try:
            m.popup(e.x_root, e.y_root)
        except Exception:
            pass

    def _img_copy_src(self, name, local=False):
        """复制图片地址：本地图给**绝对路径**（拿去别处能直接用），远程给 URL。"""
        rec = self._imgs_get().get(name)
        block = rec.get("block") if isinstance(rec, dict) else None
        if not (isinstance(block, list) and len(block) > 1
                and isinstance(block[1], dict)):
            return
        src = str(block[1].get("src") or "")
        text = (imgstore.src_path(src) or src) if local else src
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _img_upload_local(self, name):
        """本地图 → 图床：后台上传，成功后把这一枚的 src 换成 URL 并删本地文件。

        与插入时自动上传（`_img_after_insert`）走同一个落点 `_img_switch_to_remote`：
        两条路径的行为必须一致，否则「插入时传」和「右键再传」会出现两种数据形状。
        """
        rec = self._imgs_get().get(name)
        block = rec.get("block") if isinstance(rec, dict) else None
        if not (isinstance(block, list) and len(block) > 1
                and isinstance(block[1], dict)):
            return
        d = block[1]
        src = str(d.get("src") or "")
        ident = str(d.get("id") or "")
        path = imgstore.src_path(src)
        if not path or not os.path.isfile(path):
            self._show_alert("上传失败",
                             "这张图的本地文件不在了：\n%s" % (path or src), "error")
            return
        settings = (self.data.get("settings") or {}) if getattr(self, "data", None) else {}
        if not piclist.configured(settings):
            self._show_alert("上传失败",
                             "还没有配置 PicList：\n设置 → 图片与图床 → PicList路径")
            return
        note = self.current_note()
        nid = note.get("id") if note else None
        if not nid:
            return

        def work():
            return piclist.upload(settings, path)

        def done(res):
            ok, value = res if isinstance(res, tuple) else (True, res)
            if not ok:
                # 失败不动本地图：文件还在 media/ 里，正文照旧能显示
                self._show_alert("上传失败", str(value), "error")
                return
            self._img_switch_to_remote(nid, ident, value, src)
            self._show_info("已上传到图床",
                            "正文里的地址已换成：\n%s" % value)

        self._img_async(work, done)

    def _img_source_dialog(self, name):
        """弹出某张图的 Markdown 源：改地址 → 换图；只读笔记只看不改。"""
        rec = self._imgs_get().get(name)
        block = rec.get("block") if isinstance(rec, dict) else None
        if not (isinstance(block, list) and len(block) > 1
                and isinstance(block[1], dict)):
            return
        d = block[1]
        old_src = str(d.get("src") or "")
        old_alt = str(d.get("alt") or "")
        p = self.pal
        ro = self.is_readonly()

        win = self._dlg_body("图片地址")
        tk.Label(win, text="Markdown", bg=p["top"], fg=p["soft"], anchor="w",
                 font=(FONT[0], 11)).pack(fill=tk.X, padx=dpi.px(16),
                                          pady=(dpi.px(14), dpi.px(4)))
        var = tk.StringVar(value=imgstore.format_md_image(block))
        entry = tk.Entry(win, textvariable=var, font=(FONT[0], 11), bg=p["editor"],
                         fg=p["text"], insertbackground=p["accent"], relief=tk.FLAT,
                         bd=0, highlightthickness=dpi.px(1),
                         highlightbackground=p["line"], highlightcolor=p["accent"],
                         readonlybackground=p["editor"], width=52,
                         state=("readonly" if ro else tk.NORMAL))
        entry.pack(fill=tk.X, padx=dpi.px(16), ipady=dpi.px(5))
        tip = tk.Label(win,
                       text=("只读笔记，地址只能看不能改。" if ro else
                             "改方括号后面的地址（本地路径或图床 URL），确定后按新地址重新加载。"),
                       bg=p["top"], fg=p["soft"], anchor="w", justify="left",
                       font=(FONT[0], 10), wraplength=dpi.px(398))
        tip.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(6), 0))
        err = tk.Label(win, text="", bg=p["top"], fg=MENU_DANGER, anchor="w",
                       justify="left", font=(FONT[0], 10), wraplength=dpi.px(398))
        err.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(4), 0))

        state = {"busy": False}

        def close(_=None):
            win.destroy()
            return "break"

        def copy_md():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(var.get())
            except tk.TclError:
                pass

        def apply(_=None):
            if ro or state["busy"]:
                return "break"
            got = imgstore.parse_md_image_input(var.get(), old_alt)
            if got is None:
                err.configure(text="请填图片地址：本地路径或 http(s) 链接。")
                return "break"
            alt, src = got
            if src == old_src and alt == old_alt:
                close()
                return "break"
            if src == old_src:
                # 只改了 alt：不用重新加载，直接写回去
                d["alt"] = alt
                self.mark_modified()
                close()
                return "break"
            state["busy"] = True
            err.configure(text="正在加载图片…", fg=p["soft"])

            def work():
                return (True, self._img_fetch(src))

            def done(res):
                if not win.winfo_exists():
                    return                      # 加载期间用户把窗关了
                state["busy"] = False
                ok, value = res if isinstance(res, tuple) else (True, res)
                if not ok:
                    err.configure(text=str(value), fg=MENU_DANGER)
                    return
                png, w, h, final_src = value
                if not self._img_apply_source(name, block, alt, final_src,
                                              png, w, h):
                    err.configure(text="这张图片显示不出来（格式不支持或文件已损坏）。",
                                  fg=MENU_DANGER)
                    return
                win.destroy()

            self._img_async(work, done)
            return "break"

        def focus_url():
            """选中地址那一段：粘个新地址就是直接替换，不必先手工删掉。"""
            md = var.get()
            i = md.rfind("](")
            if i >= 0 and md.endswith(")"):
                try:
                    entry.selection_range(i + 2, len(md) - 1)
                    entry.icursor(len(md) - 1)
                except tk.TclError:
                    pass
            entry.focus_set()

        if ro:
            self._dlg_buttons(win, close, None, ok_text="关闭")
        else:
            btns = self._dlg_buttons(win, apply, close)
            tk.Button(btns, text="复制", width=6, bd=0, relief=tk.FLAT,
                      bg=p["editor"], fg=p["soft"], font=(FONT[0], 11),
                      cursor="hand2", activebackground=p["hover"],
                      activeforeground=p["selfg"],
                      command=copy_md).pack(side=tk.LEFT)

        win.bind("<Return>", close if ro else apply)
        win.bind("<Escape>", close)
        win.protocol("WM_DELETE_WINDOW", close)
        self._center_dialog(win, min_w=430)
        win.grab_set()
        focus_url()
        win.wait_window()

    def _img_fetch(self, src):
        """src → (png 字节, w, h, 可落库的 src)。后台线程里跑，只读文件 / 网络。

        ⚠️ 本地图片一律收进 media/ 再返回相对路径：用户的输入可能是
        `C:\\图片\\a.png`，直接存进笔记就是绝对路径 —— 换台机器断链，也正是
        imgstore「src 只存 media/ 或 URL」这条红线要挡的东西。
        图床 URL 原样保留，顺带把字节带回去落缓存，渲染时不必再下一次。
        """
        if imgstore.is_remote(src):
            raw, _w, _h = self._img_bytes_from_source(src)
            png, w, h = imgstore.normalize_image(raw)
            return png, w, h, src
        if imgstore.src_path(src) is not None:
            p = imgstore.src_path(src)
            if not os.path.isfile(p):
                raise ValueError("找不到本地图片：%s" % src)
            # media/ 里已有的：字节不用重写，但**宽度高度要读出来** —— 换地址后
            # 沿用旧尺寸的话，导出 / 占位会按错的比例走（media 里一律是归一化 PNG，
            # 读 IHDR 就够，不必过 Pillow）。
            with open(p, "rb") as f:
                w, h = imgstore.png_size(f.read(24))
            return b"", w, h, src
        raw, _w, _h = self._img_bytes_from_source(src)
        saved = imgstore.save_image(raw)    # 归一化 + 落 media/ + 去重
        return b"", saved.get("w", 0), saved.get("h", 0), saved["src"]

    def _img_apply_source(self, name, block, alt, src, png, w, h):
        """把新地址写进数据块并当场换图；显示不出来就整块回滚，返回 False。"""
        try:
            d = block[1]
        except Exception:
            return False
        backup = dict(d)
        if imgstore.is_remote(src) and png:
            # 字节已经在手上，先落缓存：否则 _img_photo 会立刻按 URL 再下一次
            self._img_cache_put(src, png)
        d["src"] = src
        d["alt"] = str(alt or "")
        if w and h:
            d["w"], d["h"] = int(w), int(h)
        photo = self._img_photo(block)
        ok = photo is not None
        if ok:
            try:
                self.text.image_configure(name, image=photo)
            except tk.TclError:
                ok = False
        if not ok:
            d.clear()
            d.update(backup)                # 回滚：正文里不能留一块空白
            return False
        self._imgs_get()[name] = {"photo": photo, "block": block}
        # 旧本地文件不在这里删：同一张图可能被别的笔记共用（hash 去重），
        # 交给落库后的 _img_gc 按全库引用统一回收。
        self.mark_modified()
        return True

    def _refresh_alarm_images(self):
        """换肤 / 确认态翻转后，就地重画正文里的每张提醒卡片。

        名字沿用旧入口（app_ui.set_theme 调它），语义已换成卡片：逐张 apply_theme
        （配色重算 + 清图标缓存）再 refresh（状态文字重算）。绝不重建正文 —— 那会
        动光标和 undo 栈。切笔记时卡片表已清空，遍历到的就是当前笔记真实存在的。
        """
        for name, card in list(self._alarm_cards_get().items()):
            try:
                if card.winfo_exists():
                    card.apply_theme(self.pal)
                    card.refresh()
            except Exception:
                pass

    def insert_alarm(self):
        """工具栏「任务提醒」：填内容 + 时间，然后在光标处放一枚小闹钟。"""
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        got = self._alarm_dialog()
        if not got:
            return
        got["id"] = alarm.new_id()
        if self._alarm_add("insert", got):
            self.mark_modified()

    def _alarm_dialog(self, data=None):
        """填「内容 + 提醒类型 + 时刻」。返回一条完整规则 dict 或 None：
        `{"text","at","repeat","hm","days"}`。

        三种类型（2026-11-18 用户定）：
          - 按时间提醒 once：日历选一天 + 时/分，响一次即止（原来的行为）；
          - 按天 daily：只要时/分，每天这时候响；
          - 按周 weekly：时/分 + 周一到周日**多选**（没有「工作日」快捷键，用户拍板去掉）。
        日期用**日历**、时间用**时/分两个 Spinbox**，都不是手打输入框：中文输入法
        下 `2026-10-30 18:00` 里的连字符和冒号十有八九会敲成全角，手打还容易写出
        `25:00`、`2月30日`；控件把范围锁死，点一下就选定。
        「只做标记不提醒」由开关表达（`at` 为 0）。
        编辑和新建共用这一个框，编辑时把原值填进去。
        """
        p = self.pal
        is_new = data is None
        data = data or {}
        at0 = data.get("at") or 0
        kind0 = alarm.repeat_kind(data)
        hm0 = alarm.hm_of(data)
        days0 = [int(x) for x in (data.get("days") or []) if str(x).isdigit()]
        if at0:
            dt = datetime.datetime.fromtimestamp(at0)
            day0 = dt.date()
            hh0, mm0 = dt.hour, dt.minute
        else:
            # 新建默认「一小时后整点」，跨零点时日期一起滚到明天（见 alarm.default_when）
            ds0, hh0, mm0 = alarm.default_when()
            day0 = (datetime.datetime.strptime(ds0, "%Y-%m-%d").date()
                    if ds0 else datetime.date.today())
        bg, border = _popover_bg(p), _popover_border(p)
        text_fg = p.get("text", "#222222")
        soft = p.get("soft", "#76767e")
        accent = p.get("accent", "#2f7cf6")
        ed = p.get("editor", "#ffffff")

        win = tk.Toplevel(self.root)
        win.title("任务提醒")
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(bg=border)
        body = tk.Frame(win, bg=bg)
        body.pack(fill=tk.BOTH, expand=True, padx=dpi.px(1), pady=dpi.px(1))

        def label(t, top=9):
            """小标题。`top` 是距上一块的上边距 —— 「提醒时间」上面压着日历 /
            星期多选，要留得比默认更开，否则时刻框像贴在前一块的底边上。"""
            lb = tk.Label(body, text=t, bg=bg, fg=soft,
                          font=(FONT[0], 9))
            lb.pack(anchor="w", padx=dpi.px(14), pady=(dpi.px(top), 0))
            return lb

        def entry(value):
            e = tk.Entry(body, bg=ed, fg=text_fg, insertbackground=accent,
                         relief=tk.FLAT, font=(FONT[0], 10), width=34,
                         highlightthickness=dpi.px(1),
                         highlightbackground=_mix_hex(ed, border, 0.7),
                         highlightcolor=accent)
            e.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(3), 0), ipady=dpi.px(3))
            e.insert(0, value)
            return e

        label("提醒什么（必填）")
        e_text = entry(data.get("text") or "")

        # ---- 提醒类型：三个并排胶囊，切换时下方区域显隐 ----
        # 用 Label 不用 Radiobutton：跟日历头部的 ‹ › 同一套扁平语言，选中态
        # 实心 accent + on_accent 文字，绝不烤死颜色（换肤跟着走）。
        label("提醒类型")
        kind_row = tk.Frame(body, bg=bg)
        kind_row.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(3), 0))
        kind_var = tk.StringVar(value=kind0)
        kind_labs = {}
        built = [False]                      # 弹框组件搭完前 toggle 直接跳过

        def paint_kinds():
            for k, lb in kind_labs.items():
                on = (kind_var.get() == k)
                lb.configure(bg=accent if on else _mix_hex(bg, text_fg, 0.06),
                             fg=on_accent(accent) if on else soft)

        for k in alarm.REPEATS:
            lb = tk.Label(kind_row, text=alarm.REPEAT_LABELS[k], bg=bg, fg=soft,
                          font=(FONT[0], 9), bd=0, padx=dpi.px(9),
                          pady=dpi.px(3), cursor="hand2")
            lb.pack(side=tk.LEFT, padx=(0, dpi.px(5)))
            lb.bind("<Button-1>", lambda _e, v=k: (kind_var.set(v), paint_kinds(),
                                                   toggle()))
            kind_labs[k] = lb
        paint_kinds()

        # ---- 日期：日历控件 ----
        # 不再用文本框手打。中文输入法下 `2026-10-30` 里的连字符常被敲成全角，
        # 而「10-30」到底是今年还是明年全靠猜；日历点一下就锁定，还顺带能看见
        # 今天/明天在星期的哪个位置。固定 6 行（见 alarm.month_grid），翻月时
        # 高度不跳，‹ › 不会上下窜。
        date_lab = label("提醒日期")
        chosen = [day0]                      # 选中的那一天
        shown = [day0.year, day0.month]      # 当前显示的月份（翻月后与 chosen 不同）
        nums = [alarm.month_grid(shown[0], shown[1])]
        cal_on = [True]                      # 「到点提醒」关掉后日历一起灰掉

        cal = tk.Frame(body, bg=bg)
        cal.pack(fill=tk.X, padx=dpi.px(10), pady=(dpi.px(2), 0))

        def mini(parent, text, cmd, size=13, color=None):
            """日历头部的小按钮（‹ › 今天）。用 Label 而不是 Button：
            Button 自带边框、内边距和按下高亮，在这个扁平的对话框里很扎眼。"""
            lb = tk.Label(parent, text=text, bg=bg, fg=color or soft,
                          font=(FONT[0], size), bd=0, padx=dpi.px(7), pady=0,
                          cursor="hand2")
            lb.bind("<Button-1>", lambda _e: cmd())
            lb.bind("<Enter>", lambda _e: lb.configure(fg=accent))
            lb.bind("<Leave>", lambda _e: lb.configure(fg=color or soft))
            return lb

        head = tk.Frame(cal, bg=bg)
        head.pack(fill=tk.X)

        def turn(delta):
            if not cal_on[0]:
                return                   # 灰掉时连翻月也不让：只看不选比不能选更迷惑
            y, m = alarm.shift_month(shown[0], shown[1], delta)
            if not (1900 <= y <= 2100):
                return
            shown[0], shown[1] = y, m
            nums[0] = alarm.month_grid(y, m)
            paint()

        def goto_today():
            if not cal_on[0]:
                return
            t = datetime.date.today()
            chosen[0] = t
            shown[0], shown[1] = t.year, t.month
            nums[0] = alarm.month_grid(t.year, t.month)
            paint()

        mini(head, "今天", goto_today, size=9, color=accent).pack(side=tk.RIGHT,
                                                                padx=(0, dpi.px(6)))
        # 先左后右地 pack：‹ 最左、标题居中、› 紧跟。标题给固定 width，翻月时
        # 字数变化（10月→11月）不会把两个箭头推来推去。
        mini(head, "‹", lambda: turn(-1)).pack(side=tk.LEFT, padx=(dpi.px(6), 0))
        ttl = tk.Label(head, text="", bg=bg, fg=text_fg, font=(FONT[0], 10),
                       bd=0, padx=0, pady=0, width=9, anchor="w")
        ttl.pack(side=tk.LEFT)
        mini(head, "›", lambda: turn(1)).pack(side=tk.LEFT)

        net = tk.Frame(cal, bg=bg)
        net.pack(fill=tk.X, pady=(dpi.px(1), 0))
        for c in range(7):
            # uniform 让七列等宽：否则「30」那种两位数会把整列撑宽，格子歪掉
            net.columnconfigure(c, weight=1, uniform="cal")
        for c, wtxt in enumerate(alarm.WEEKDAYS):
            tk.Label(net, text=wtxt, bg=bg, fg=soft, font=(FONT[0], 8),
                     bd=0, padx=0, pady=0).grid(row=0, column=c, pady=(0, dpi.px(1)))

        cells = []
        for r in range(6):
            for c in range(7):
                lb = tk.Label(net, text="", bg=bg, fg=text_fg,
                              font=(FONT[0], 9), bd=0, padx=0, pady=0)
                lb.grid(row=r + 1, column=c, sticky="nsew", ipady=dpi.px(1))
                cells.append(lb)

        def paint_cell(i, hovered=False):
            lb, d = cells[i], nums[0][i]
            if not d:
                lb.configure(text="", bg=bg, cursor="")
                return
            cur = datetime.date(shown[0], shown[1], d)
            if cur == chosen[0]:
                # 选中的一天：实心 accent + 自动对比的文字色（金色 accent 上白字会糊）
                lb.configure(text=str(d), bg=accent, fg=on_accent(accent),
                             cursor="hand2" if cal_on[0] else "")
            elif hovered and cal_on[0]:
                lb.configure(text=str(d), bg=_mix_hex(bg, text_fg, 0.10),
                             fg=text_fg, cursor="hand2")
            else:
                fg = accent if cur == datetime.date.today() else (
                    text_fg if cal_on[0] else soft)
                lb.configure(text=str(d), bg=bg, fg=fg,
                             cursor="hand2" if cal_on[0] else "")

        def paint():
            ttl.configure(text="%d年%d月" % (shown[0], shown[1]))
            for i in range(42):
                paint_cell(i)

        def pick(i):
            d = nums[0][i]
            if not d or not cal_on[0]:
                return
            chosen[0] = datetime.date(shown[0], shown[1], d)
            paint()

        for i, lb in enumerate(cells):
            lb.bind("<Button-1>", lambda _e, k=i: pick(k))
            lb.bind("<Enter>", lambda _e, k=i: paint_cell(k, True))
            lb.bind("<Leave>", lambda _e, k=i: paint_cell(k))

        paint()

        # ---- 按周：周一到周日多选（用户拍板：不做「工作日」快捷键）----
        days_row = tk.Frame(body, bg=bg)
        days_lab = tk.Label(days_row, text="每周哪几天（可多选）", bg=bg, fg=soft,
                            font=(FONT[0], 9), bd=0, anchor="w")
        days_lab.pack(anchor="w")
        day_grid = tk.Frame(days_row, bg=bg)
        day_grid.pack(fill=tk.X, pady=(dpi.px(3), 0))
        for c in range(7):
            day_grid.columnconfigure(c, weight=1, uniform="wd")
        days_sel = set(days0) if days0 else {0}   # 默认周一，避免空选
        day_labs = {}

        def paint_days():
            for i, lb in day_labs.items():
                on = i in days_sel
                lb.configure(bg=accent if on else _mix_hex(bg, text_fg, 0.06),
                             fg=on_accent(accent) if on else soft)

        for i, wtxt in enumerate(alarm.WEEKDAYS):
            lb = tk.Label(day_grid, text=wtxt, bg=bg, fg=soft, font=(FONT[0], 10),
                          bd=0, width=2, cursor="hand2")
            lb.grid(row=0, column=i, padx=(0, dpi.px(3)), ipady=dpi.px(2))

            def toggle_day(_e=None, k=i):
                if k in days_sel:
                    days_sel.discard(k)
                else:
                    days_sel.add(k)
                paint_days()
            lb.bind("<Button-1>", toggle_day)
            day_labs[i] = lb
        paint_days()

        # 时间行与上方日历 / 星期多选之间留出明显的呼吸区（用户 2026-11-18 要求）
        time_lab = label("提醒时间", top=18)
        trow = tk.Frame(body, bg=bg)
        trow.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(5), 0))

        def spin(lo, hi, value):
            """时/分选择框。format 两位补零，防止显示出 `8` 这种不齐的数字。"""
            sp = tk.Spinbox(trow, from_=lo, to=hi, increment=1, wrap=True,
                            format="%02.0f", width=2, font=(FONT[0], 11),
                            bd=0, relief=tk.FLAT, justify="center",
                            bg=ed, fg=text_fg, insertbackground=accent,
                            buttonbackground=_mix_hex(ed, text_fg, 0.10),
                            disabledbackground=bg, disabledforeground=soft,
                            highlightthickness=dpi.px(1),
                            highlightbackground=_mix_hex(ed, border, 0.7),
                            highlightcolor=accent)
            sp.delete(0, tk.END)
            sp.insert(0, "%02d" % value)
            sp.pack(side=tk.LEFT, ipady=dpi.px(2))
            return sp

        sp_h = spin(0, 23, hh0)
        tk.Label(trow, text=":", bg=bg, fg=text_fg,
                 font=(FONT[0], 11)).pack(side=tk.LEFT, padx=dpi.px(2))
        sp_m = spin(0, 59, mm0)

        def set_now():
            n = datetime.datetime.now()
            for sp, v in ((sp_h, n.hour), (sp_m, n.minute)):
                sp.delete(0, tk.END)
                sp.insert(0, "%02d" % v)

        btn_now = tk.Button(trow, text="现在", command=set_now, relief=tk.FLAT,
                            bd=0, bg=_mix_hex(bg, text_fg, 0.10), fg=text_fg,
                            activebackground=_mix_hex(bg, text_fg, 0.18),
                            activeforeground=text_fg, font=(FONT[0], 9),
                            padx=dpi.px(9), pady=dpi.px(1), cursor="hand2")
        btn_now.pack(side=tk.LEFT, padx=(dpi.px(8), 0))

        # 到点提醒：不勾就是「只做标记」（at=0，永远不响）。原来的表达方式是
        # 「时间框留空」，换成 Spinbox 之后没有空值，只能用一个显式开关。
        var_on = tk.BooleanVar(value=True if is_new else bool(at0))
        chk = tk.Checkbutton(trow, text="到点提醒", variable=var_on, bg=bg,
                             fg=text_fg, font=(FONT[0], 9), bd=0,
                             activebackground=bg, activeforeground=text_fg,
                             selectcolor=_mix_hex(bg, text_fg, 0.12),
                             cursor="hand2", highlightthickness=0)
        chk.pack(side=tk.LEFT, padx=(dpi.px(14), 0))

        def toggle(*_a):
            # 类型决定显示哪块：once 才要日历；weekly 才要星期多选。
            # pack 顺序必须让这两块都排在 trow 之前，否则时间行会跳到最上面。
            # 弹框构建期间（trow 还没建）被类型按钮点到时直接跳过，等构建末尾
            # 统一 toggle 一次 —— 否则这里会引用尚未赋值的局部变量。
            if not built[0]:
                return
            kind = kind_var.get()
            for w in (date_lab, cal, days_row):
                if w is not None:
                    w.pack_forget()
            if kind == alarm.REPEAT_ONCE:
                # ⚠️ date_lab 也要 before=trow：不写就追加到 pack 队列末尾，
                # 标题会跑到「取消 / 确定」下面去（2026-11-18 探针发现）
                date_lab.pack(anchor="w", padx=dpi.px(14), pady=(dpi.px(9), 0),
                              before=trow)
                cal.pack(fill=tk.X, padx=dpi.px(10), pady=(dpi.px(2), 0),
                         before=trow)
            elif kind == alarm.REPEAT_WEEKLY:
                days_row.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(9), 0),
                              before=trow)
            # ⚠️ 时间标题必须**重新** pack 到 trow 之前：它建得比这两块早，
            # 而 before=trow 只会插到它后面 —— 不重排就会出现「提醒时间」悬在
            # 星期 / 日历上方、和时刻框被拆成两处的错位。
            time_lab.pack(anchor="w", padx=dpi.px(14), pady=(dpi.px(18), 0),
                          before=trow)
            # 「到点提醒」关掉后日期 / 星期一起灰掉，免得能编辑却不生效
            cal_on[0] = bool(var_on.get())
            st = tk.NORMAL if cal_on[0] else tk.DISABLED
            for w in (sp_h, sp_m, btn_now):
                w.configure(state=st)
            for lb in day_labs.values():
                lb.configure(cursor="hand2" if cal_on[0] else "")
            paint()

        chk.configure(command=toggle)
        built[0] = True
        toggle()

        err = tk.Label(body, text="", bg=bg, fg=p.get("danger", "#c0392b"),
                       font=(FONT[0], 9), anchor="w")
        err.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(7), 0))

        row = tk.Frame(body, bg=bg)
        row.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(9), dpi.px(12)))
        out = [None]

        def ok(_e=None):
            txt = alarm.normalize_text(e_text.get())
            if not txt:
                err.configure(text="请填写提醒内容")
                e_text.focus_set()
                return "break"
            kind = kind_var.get()
            if kind == alarm.REPEAT_WEEKLY and var_on.get() and not days_sel:
                err.configure(text="请至少选一天（周一到周日）")
                return "break"
            hm = ""
            if var_on.get():
                # Spinbox 是可编辑的：上限只约束箭头，手输 `99` 仍可能进得来
                try:
                    hh, mm = int(sp_h.get()), int(sp_m.get())
                except ValueError:
                    err.configure(text="时间请填 0-23 时、0-59 分")
                    return "break"
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    err.configure(text="时间超出范围了（00:00 - 23:59）")
                    return "break"
                hm = "%02d:%02d" % (hh, mm)
                if kind == alarm.REPEAT_ONCE:
                    ts, msg = alarm.parse_when(chosen[0].strftime("%Y-%m-%d"),
                                               "%02d:%02d" % (hh, mm))
                else:
                    # 重复提醒的 at 只是「从哪天起」的锚点（今天 0 点），
                    # 真正的触发时刻由 hm + 周期算出，见 alarm.slot_date
                    today = datetime.date.today()
                    ts, msg = datetime.datetime(today.year, today.month,
                                                today.day).timestamp(), ""
            else:
                ts, msg = 0.0, ""
            if msg:
                err.configure(text=msg)
                return "break"
            got = {"text": txt, "at": ts}
            got.update(alarm.normalize_rule(kind, hm, sorted(days_sel)))
            out[0] = got
            win.destroy()
            return "break"

        def cancel(_e=None):
            win.destroy()
            return "break"

        tk.Button(row, text="确定", command=ok, relief=tk.FLAT, bd=0,
                  bg=accent, fg=on_accent(accent), activebackground=accent,
                  activeforeground=on_accent(accent), font=(FONT[0], 10),
                  padx=dpi.px(16), pady=dpi.px(4), cursor="hand2").pack(side=tk.RIGHT)
        tk.Button(row, text="取消", command=cancel, relief=tk.FLAT, bd=0,
                  bg=_mix_hex(bg, text_fg, 0.10), fg=text_fg,
                  activebackground=_mix_hex(bg, text_fg, 0.18),
                  activeforeground=text_fg, font=(FONT[0], 10),
                  padx=dpi.px(16), pady=dpi.px(4), cursor="hand2").pack(side=tk.RIGHT, padx=(0, dpi.px(8)))

        for w in (e_text, sp_h, sp_m):
            w.bind("<Return>", ok)
            w.bind("<Escape>", cancel)
        # 日历格子不拿焦点，回车/ESC 时焦点还在上面的控件里；绑在窗口上兜住
        # 其余情况（点过日历之后焦点若落到 body 上）。子控件先处理并 break，
        # 所以不会触发两次。
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            win.geometry("+%d+%d" % (rx + max(0, (rw - w) // 2),
                                     ry + max(30, (rh - h) // 3)))
        except Exception:
            pass
        e_text.focus_set()
        e_text.icursor(tk.END)
        try:
            win.grab_set()
            self.root.wait_window(win)
        except Exception:
            pass
        return out[0]

    def _alarm_edit(self, name):
        if self._refuse_readonly():
            return
        data = self._alarms_get().get(name)
        if not data:
            return
        got = self._alarm_dialog(data)
        if not got:
            return
        # 整条规则一起换：改类型 / 改星期都得落库，不能只写 text/at
        keep = {"id": data.get("id")}
        data.clear()
        data.update(keep)
        data.update(got)
        # 规则改了 → 旧槽位的 fired/acked 不再对应，清掉免得新周期被旧键挡住
        for m in (self._alarm_fired_map(), self._alarm_acked_map()):
            for k in [k for k in m if k.startswith(self._alarm_key(data) + "@")]:
                m.pop(k, None)
        card = self._alarm_cards_get().get(name)
        if card is not None:
            try:
                card.refresh()
            except Exception:
                pass
        self.mark_modified()

    def _alarm_pos(self, name):
        """这张卡片在正文里的索引；找不到返回 None。"""
        card = self._alarm_cards_get().get(name)
        if card is None:
            return None
        try:
            return self.text.index(card)
        except Exception:
            return None

    def _alarm_remove(self, name):
        """删掉卡片 = 取消这条提醒（数据随卡片一起走）。"""
        if self._refuse_readonly():
            return
        pos = self._alarm_pos(name)
        if not pos:
            return
        self._alarm_tip_hide()
        self.text.config(state=tk.NORMAL)
        self.text.edit_separator()
        try:
            self.text.delete(pos)
            # 连同卡片后那条独占行的换行一起删（和代码块同一套），否则每删一张
            # 提醒就留一个空行。换行本身是 serialize 记录的普通文本。
            if self.text.get(pos, "%s +1c" % pos) == "\n":
                self.text.delete(pos)
        except Exception:
            return
        finally:
            self.text.edit_separator()
        self._alarms_get().pop(name, None)
        self._alarm_cards_get().pop(name, None)
        self.mark_modified()

    def _alarm_ack_card(self, name):
        """卡片上点「完成」＝ 写 alarm_acked（与弹窗的 _ack 同一条确认路径）。"""
        if self._refuse_readonly():
            return
        data = self._alarms_get().get(name)
        if not data:
            return
        # 重复提醒只确认「当前周期」那一格，下个周期自然回到待提醒
        self._alarm_acked_map()[self._alarm_state_key(data)] = time.time()
        try:
            save_data(self.data)
        except Exception:
            pass
        self._refresh_alarm_index()
        card = self._alarm_cards_get().get(name)
        if card is not None:
            try:
                card.refresh()
            except Exception:
                pass
        if self.data.get("current_note") == ALARM_INDEX_ID:
            self.load_editor()

    # ----- 悬浮提示与右键 -----

    def _alarm_at(self, x, y):
        """(x, y) 落在一张提醒卡片上时返回它的 name，否则 None。

        卡片本身已经把任务/时间/状态写在脸上，悬浮提示这里只补「响过没有」这类
        运行时状态。命中仍走正文坐标（卡片是 window，dump 里按 window 分支找）。
        """
        try:
            pos = self.text.index("@%d,%d" % (int(x), int(y)))
            for key, value, _i in self.text.dump(pos, "%s +1c" % pos, window=True):
                if key == "window":
                    try:
                        w = self.text.nametowidget(value)
                    except Exception:
                        continue
                    if isinstance(w, AlarmCard) and w.name in self._alarms_get():
                        return w.name
        except Exception:
            pass
        return None

    def _on_alarm_motion(self, event=None):
        """指针扫过正文：停在闹钟上就浮出提示，移开就收起。"""
        if event is None:
            return
        name = self._alarm_at(event.x, event.y)
        if name == getattr(self, "_alarm_hover", None):
            return                          # 还在同一枚上，别反复重建浮窗
        self._alarm_hover = name
        if not name:
            self._alarm_tip_hide()
            return
        data = self._alarms_get().get(name) or {}
        title, sub = alarm.tips(data, self._alarm_is_fired(data))
        self._alarm_tip_show(event.x_root + dpi.px(14), event.y_root + dpi.px(18),
                             title, sub)

    def _alarm_tip_show(self, x, y, title, sub):
        p = self.pal
        self._alarm_tip_hide()
        try:
            win = tk.Toplevel(self.root)
            win.wm_overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=_popover_border(p))
            body = tk.Frame(win, bg=_popover_bg(p))
            body.pack(padx=dpi.px(1), pady=dpi.px(1))
            # ⚠️ Label 的 padx/pady 只吃单值（"6 0" 会报 bad screen distance），
            # 上下不对称要靠 pack 的 pady 来表达
            for txt, fg, size, gap in ((title, p.get("text", "#222222"), 10,
                                        (dpi.px(6), 0)),
                                       (sub, p.get("soft", "#76767e"), 9,
                                        (dpi.px(1), dpi.px(6)))):
                tk.Label(body, text=txt, bg=_popover_bg(p), fg=fg,
                         font=(FONT[0], size), bd=0, justify="left", anchor="w",
                         wraplength=dpi.px(300), padx=dpi.px(10)).pack(fill=tk.X, pady=gap)
            win.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            # 夹进**闹钟所在那块屏**的工作区（多屏时 winfo_screenwidth 只报主屏，
            # 副屏上悬停会把它拽到主屏左缘）
            area = win_frame.work_area_at(x, y, fallback=win)
            px, py = win_frame.clamp_pos(area, x, y, w, h, pad=dpi.px(4))
            win.geometry("+%d+%d" % (px, py))
            self._alarm_tip_win = win
        except Exception:
            self._alarm_tip_win = None

    def _alarm_tip_hide(self):
        """只收起浮窗。

        ⚠️ 别在这里顺手把 `_alarm_hover` 清成 None：它记的是「指针当前压在哪
        一枚闹钟上」，`_on_alarm_motion` 靠它做去重。这里清了，下一次移动时
        `name == None == _alarm_hover` 成立就早退，提示再也不会收起 —— 实测踩过。
        """
        win = getattr(self, "_alarm_tip_win", None)
        self._alarm_tip_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _on_alarm_menu(self, event=None):
        """右键：编辑 / 删除。不是闹钟就把事件让回去（正文本来没有右键菜单）。"""
        if event is None:
            return None
        name = self._alarm_at(event.x, event.y)
        if not name:
            return None
        self._alarm_tip_hide()
        # 菜单关掉后指针往往还压在同一枚上：重置它，回来时提示才会重新浮出
        self._alarm_hover = None
        m = PopupMenu(self.root, self.pal)
        ro = self.is_readonly()
        m.add("编辑提醒", enabled=not ro, command=lambda: self._alarm_edit(name))
        data = self._alarms_get().get(name) or {}
        if self._alarm_card_state(data) == alarm.STATE_PENDING:
            m.add("完成（记为已确认）", enabled=not ro,
                  command=lambda: self._alarm_ack_card(name))
        m.add("删除提醒", danger=True, enabled=not ro,
              command=lambda: self._alarm_remove(name))
        m.popup(event.x_root, event.y_root)
        return "break"

    # ----- 「响过没有」的状态 -----

    def _alarm_fired_map(self):
        """`{"<note_id>|<alarm_id>": 时间戳}`，落在 settings 里跟库走。"""
        st = self.data.setdefault("settings", {})
        m = st.get("alarm_fired")
        if not isinstance(m, dict):
            m = st["alarm_fired"] = {}
        return m

    def _alarm_key(self, data, note_id=None):
        nid = note_id or ((self.current_note() or {}).get("id") or "")
        return "%s|%s" % (nid, data.get("id") or "")

    def _alarm_is_fired(self, data):
        return bool(self._alarm_fired_map().get(self._alarm_state_key(data)))

    def _alarm_acked_map(self):
        """`{"<note_id>|<alarm_id>": 时间戳}`：用户**真点过弹窗「知道了」**才算已确认。

        与 alarm_fired 分开存（2026-11-09 用户定义）：fired 是「系统处理过这条
        提醒」，里面还包括关机期间过期、回来自动补记的 stale —— 那些没有人
        确认过，混进来「已确认」就成了假的。两个都是本机会话状态，跟库走、
        不进备份（见 backup.STATE_KEYS）。
        """
        st = self.data.setdefault("settings", {})
        m = st.get("alarm_acked")
        if not isinstance(m, dict):
            m = st["alarm_acked"] = {}
        return m

    # ----- 到点提醒：单个轮询，扫全库 -----

    def _alarm_bind(self):
        """把悬浮 / 右键挂到正文，并起一次轮询。重复调用无副作用。"""
        if getattr(self, "_alarm_bound", False):
            return
        self._alarm_bound = True
        self._alarm_hover = None
        self._alarm_tip_win = None
        try:
            self.text.bind("<Motion>", self._on_alarm_motion, add="+")
            self.text.bind("<Button-3>", self._on_alarm_menu, add="+")
            self.text.bind("<Leave>", lambda _e: self._alarm_tip_hide(), add="+")
        except Exception:
            pass
        self._alarm_prune_keys()
        self._alarm_job = None
        self._alarm_start_tick()

    def _alarm_prune_keys(self):
        """清掉过期槽位键：重复提醒每天都生一个 @2026-11-18，
        不清的话 settings 会无限长（一年 365 条 × 提醒数）。只保留近 30 天。"""
        try:
            cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
            for m in (self._alarm_fired_map(), self._alarm_acked_map()):
                # once 的旧键没有 @，保留；带 @ 的只留日期 >= cutoff
                gone = []
                for k in m:
                    if not isinstance(k, str) or "@" not in k:
                        continue
                    tail = k.rsplit("@", 1)[1]
                    if tail != "start" and tail < cutoff:
                        gone.append(k)
                for k in gone:
                    m.pop(k, None)
        except Exception:
            pass

    def _alarm_start_tick(self):
        if getattr(self, "_alarm_job", None) is None:
            self._alarm_job = self._after(1200, self._alarm_tick)

    def _alarm_tick(self):
        """自己排下一次 —— 用 _after 而不是 root.after，窗口销毁后会被统一取消。"""
        self._alarm_job = None
        if not getattr(self, "_alive", False):
            return
        try:
            self._alarm_check()
        except Exception:
            pass
        self._alarm_job = self._after(self.ALARM_TICK_MS, self._alarm_tick)

    def _alarm_check(self):
        """扫全库找到点的闹钟：该弹的弹一个，过期太久只记状态（见 alarm.GRACE）。"""
        # 当前笔记的改动可能还压在 600ms 去抖里，先落一次再扫，否则新设的闹钟
        # 要等到下一次编辑才可能被扫到
        if getattr(self, "_save_job", None) is not None:
            try:
                self._flush_save()
            except Exception:
                pass
        now = time.time()
        fired = self._alarm_fired_map()
        items = []
        for n in self.data.get("notes") or []:
            nid = n.get("id")
            for it in n.get("content") or []:
                if it[0] != "alarm" or len(it) < 2 or not isinstance(it[1], dict):
                    continue
                d = it[1]
                if d.get("at"):
                    items.append((nid, d))
        # 过滤「当前周期已响过」交给 due_all：重复提醒的键带 @日期，
        # 今天响过不会挡住明天（一次性提醒仍是旧键，行为完全一致）。
        # acked 也一起传进去：在正文卡片上点过「完成」的，到点就不再弹窗。
        fire, stale = alarm.due_all(items, fired,
                                    lambda nid, d: self._alarm_key(d, nid), now,
                                    acked=self._alarm_acked_map())
        changed = False
        for nid, d, key in stale:
            fired[key] = now
            changed = True
        if fire:
            nid, d, key = fire[0]       # 一轮只弹一条，剩下的下一轮排队
            fired[key] = now
            changed = True
            self._alarm_popup(nid, d)
            self._alarm_hover = None    # 状态变了，下次悬停要重算文案
        if changed:
            try:
                save_data(self.data)
            except Exception:
                pass
        # 当前笔记的卡片要跟着时钟翻面（待提醒 → 已过期）：只就地 refresh 那几张
        # 状态真变了的，绝不重建正文（会动光标和 undo 栈）。20 秒一轮，代价可忽略。
        for name, card in list(self._alarm_cards_get().items()):
            try:
                if not card.winfo_exists():
                    continue
                fresh = self._alarm_card_state(card.data)
                if fresh != card._state:
                    card.refresh(fresh)
            except Exception:
                pass
        # 「已完成」跟着时钟走：汇总表常开时，提醒时刻翻过去的那一分钟也要
        # 把「否」改成「是」。只在正文真的变了才重载编辑器（只读笔记，不会打断输入）。
        if self.data.get("current_note") == ALARM_INDEX_ID:
            idx = next((x for x in self.data["notes"]
                        if x.get("id") == ALARM_INDEX_ID), None)
            if idx is not None:
                old = idx.get("content")
                self._refresh_alarm_index()
                if idx.get("content") != old:
                    self.load_editor()

    def _alarm_popup(self, note_id, data):
        """到点浮窗：置顶小卡片，一个「知道了」。"""
        p = self.pal
        bg, border = _popover_bg(p), _popover_border(p)
        accent = p.get("accent", "#2f7cf6")
        win = tk.Toplevel(self.root)
        try:
            win.overrideredirect(True)
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg=border)
        body = tk.Frame(win, bg=bg)
        body.pack(padx=dpi.px(1), pady=dpi.px(1))
        # Label 的 padx/pady 只吃单值，上下留白一律走 pack 的 pady
        tk.Label(body, text="任务提醒 · " + (alarm.fmt_at(data.get("at")) or "未设时间"),
                 bg=bg, fg=accent, font=(FONT[0], 9), bd=0,
                 padx=dpi.px(14), anchor="w").pack(fill=tk.X, pady=(dpi.px(10), 0))
        tk.Label(body, text=data.get("text") or "", bg=bg,
                 fg=p.get("text", "#222222"), font=(FONT[0], 11), bd=0,
                 padx=dpi.px(14), anchor="w", justify="left",
                 wraplength=dpi.px(260)).pack(fill=tk.X, pady=(dpi.px(2), 0))
        tk.Label(body, text="点「知道了」会记为已确认，并同步到任务提醒列表",
                 bg=bg, fg=p.get("soft", "#888888"), font=(FONT[0], 8), bd=0,
                 padx=dpi.px(14), anchor="w").pack(fill=tk.X, pady=(dpi.px(3), 0))
        row = tk.Frame(body, bg=bg)
        row.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(9), dpi.px(11)))

        def _ack():
            # ⚠️ 只在这里写 alarm_acked：_alarm_check 里那些「过期自动补记」的
            # stale 只进 alarm_fired，绝不能顺手算成用户确认过
            key = alarm.state_key("%s|%s" % (note_id, data.get("id") or ""), data)
            self._alarm_acked_map()[key] = time.time()
            try:
                save_data(self.data)
            except Exception:
                pass
            self._refresh_alarm_index()
            # 卡片正开着就就地翻到「已确认」；汇总表正开着就立刻重刷
            for name, card in list(self._alarm_cards_get().items()):
                if self._alarm_state_key(card.data) == key:
                    try:
                        card.refresh(alarm.STATE_ACKED)
                    except Exception:
                        pass
            if self.data.get("current_note") == ALARM_INDEX_ID:
                self.load_editor()
            try:
                win.destroy()
            except Exception:
                pass

        tk.Button(row, text="知道了", command=_ack, relief=tk.FLAT, bd=0,
                  bg=accent, fg=on_accent(accent), activebackground=accent,
                  activeforeground=on_accent(accent), font=(FONT[0], 9),
                  padx=dpi.px(14), pady=dpi.px(3), cursor="hand2").pack(side=tk.RIGHT)
        try:
            win.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            # 摆在**主窗口所在那块屏**的右下角。取工作区（已扣任务栏）再留 66px，
            # 100% 缩放下与旧值（屏幕高 - 96）落在同一个位置；多屏时不会跑到主屏去。
            ax, ay, aw, ah = win_frame.work_area(self.root)
            win.geometry("+%d+%d" % (ax + aw - w - 28, ay + ah - h - dpi.px(66)))
        except Exception:
            pass
        return win

    # ---------- 笔记加密 ----------
    # 生命周期：
    #   设置口令 → sec（密文）落在笔记里，content 仍留在内存（刚设完就是解锁态）
    #   切换走   → _seal_prev_note()：重新加密 + 清掉内存明文 + 丢掉缓存的密钥
    #   切回来   → _ensure_unlocked()：弹口令框，验签通过才把明文解出来
    #   编辑中   → mark_modified() 每次都重新加密（用缓存密钥，不跑 PBKDF2）
    # 所以数据库里任何时刻都只有密文，content/plain 两列对加密笔记恒为空。

    def _pick_note(self, nid=None):
        if nid:
            return next((x for x in self.data["notes"] if x["id"] == nid), None)
        return self.current_note()

    def _sync_editor_to(self, n):
        """要操作的就是正在编辑的这一篇时，先把编辑器里的内容同步进内存。"""
        if n and self.data.get("current_note") == n["id"]:
            n["content"] = self.serialize()

    def _head_char(self, n):
        """正文第一行（标题）的第一个字符 —— 锁着的时候列表要显示「它 + 星号」。"""
        try:
            for ln in self._lines(n):
                s = ln.strip()
                if s:
                    return s[0]
        except Exception:
            pass
        return ""

    def _seal_into(self, n):
        """用缓存的密钥把明文重新加密进 n["sec"]（内存里的 content 保持不动）。"""
        key = self._note_key(n["id"])
        old = n.get("sec")
        if not key or not secret.is_blob(old):
            return False
        try:
            plain = json.dumps(n.get("content") or [], ensure_ascii=False)
        except Exception:
            return False
        blob = secret.reseal_with_key(plain, old, key)
        # 标题首字跟着密文走：每次重新加密都按当前正文重算（标题可能已经被改写）。
        # 万一此刻读不出标题（比如 content 刚被清空），沿用旧的那个字，别丢。
        secret.stamp_hint(blob, self._head_char(n) or secret.hint_of(old))
        n["sec"] = blob
        return True

    def _try_open(self, n, pw):
        """拿给定口令试着开一篇加密笔记。成功 → 明文进内存 + 缓存密钥，返回 True。

        与弹框那条路共用同一段代码：口令池里静默试、用户手输，走完全一样的流程，
        不会出现「认得口令池里的、认不得手输的」这种差别。
        """
        if not n or not secret.is_blob(n.get("sec")):
            return False
        try:
            plain, key = secret.open_sealed(n["sec"], pw)
            content = json.loads(plain)
        except secret.BadPassword:
            return False
        except Exception:
            return False        # 密文损坏 / 不是合法 JSON：一律当作打不开
        n["content"] = content if isinstance(content, list) else []
        self._keys[n["id"]] = key
        # 记住这个口令：它就是「当前这篇」的口令，下一篇同口令时可以直接开
        self._cur_pw = pw
        # 老数据（升级前就加密的）第一次成功解锁时补上标题首字，之后列表里
        # 显示的就是「首字 + 星号」而不是清一色的占位。save_data 留给调用方。
        if not secret.hint_of(n["sec"]):
            secret.stamp_hint(n["sec"], self._head_char(n))
        return True

    def _ensure_unlocked(self, n, force=False):
        """确保这篇加密笔记在内存里有明文，返回 True = 可读。

        口令错了可以重试（最多 3 次）；取消就返回 False，界面按「已加密 · 未解锁」
        处理 —— 不展示任何内容，也不允许编辑。

        免输只认一种情形：**当前这篇**用的就是同一个口令（`_cur_pw`）。因为每篇笔记
        的 salt 不同、派生密钥无法互通，判断「口令是不是同一个」只能真的拿它去解一次
        （一次 PBKDF2，几十毫秒）。除此之外一律照常询问 —— 包括从明文笔记切过来、
        重开程序、以及口令真的不同这三种情况。
        """
        if not n or not secret.is_blob(n.get("sec")):
            return True
        if not force and n.get("content") and self._note_key(n["id"]):
            return True
        pw = self._cur_pw
        if pw:
            self._cur_pw = None         # 先作废：解不开就不能让它继续代表「当前这篇」
            if self._try_open(n, pw):
                return True             # _try_open 成功时会把 _cur_pw 补回来
            # 解不开：口令不同（或密文坏了），往下走正常询问流程
        for _ in range(3):
            flag = {}
            # 只有带恢复槽的笔记才显示这条入口，免得给每篇都挂一个点进去
            # 必定失败的按钮。点了它走完整的「验码 → 重设口令」流程。
            pw = self._ask_password(
                "已加密笔记", "这篇笔记已加密，请输入口令：",
                out=flag if secret.has_recovery(n.get("sec")) else None)
            if flag.get("recovery"):
                return self.recover_note_with_code(n)
            if pw is None:
                return False
            if self._try_open(n, pw):
                return True
            self._show_error("无法打开",
                             "口令不正确，或这篇笔记的密文已损坏。")
        return False

    def _ask_pw_twice(self, title, head):
        """连续输两次口令，一致则返回口令，否则 None（并说明原因）。"""
        pw = self._ask_password(title, head)
        if pw is None:
            return None
        if len(pw) < 4:
            self._show_info("口令太短", "口令至少要 4 个字符。")
            return None
        pw2 = self._ask_password(title, "再输入一次同样的口令：")
        if pw2 is None:
            return None
        if pw2 != pw:
            self._show_error("口令不一致", "两次输入的口令不同，未做任何修改。")
            return None
        return pw

    def _offer_recovery_code(self, n, blob, key, replacing=False):
        """设 / 改口令后生成**本篇**恢复码，展示一次；勾选「已抄下」才写槽。

        返回规范化后的码（供调用方在提交前自检），未启用返回 None。程序只落
        封装后的密文，恢复码本身用完即弃，绝不进 settings / prefs / 备份元数据。
        """
        code = secret.new_recovery_code()
        ok = self._show_recovery_code(code, secret.recovery_id(code),
                                      _note_head(n), replacing=replacing)
        if not ok:
            return None
        try:
            secret.seal_recovery(blob, key, code)
        except (ValueError, TypeError):
            self._show_error("恢复码",
                             "恢复槽生成失败：这篇笔记仍已加密，但没有恢复能力。")
            return None
        return secret.norm_recovery_code(code)

    def _apply_new_seal(self, n, blob, key, pw, content=None):
        """把重设 / 新建的密文一次性装回笔记（恢复流程与普通流程共用）。"""
        n["sec"] = blob
        if content is not None:
            n["content"] = content
        self._keys[n["id"]] = key
        self._cur_pw = pw          # 新口令就是「当前这篇」的口令，不是恢复码
        save_data(self.data)
        self.refresh_notes()
        self._refresh_folder_counts()

    def set_note_password(self, nid=None):
        """给笔记设置口令（已有口令则改为「修改口令」）。"""
        n = self._pick_note(nid)
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self.is_protected_note(n):
            self._show_info("无法加密", "系统笔记是只读的，不需要（也不能）加密。")
            return
        if secret.is_blob(n.get("sec")):
            self.change_note_password(n["id"])
            return
        self._sync_editor_to(n)
        pw = self._ask_pw_twice(
            "设置加密口令",
            "为这篇笔记设置口令。\n\n"
            "口令只存在你的脑子里——忘记之后，笔记内容无法恢复。")
        if not pw:
            return
        blob, key = secret.seal_and_key(
            json.dumps(n.get("content") or [], ensure_ascii=False), pw)
        secret.stamp_hint(blob, self._head_char(n))
        enabled = self._offer_recovery_code(n, blob, key)
        self._apply_new_seal(n, blob, key, pw)
        # 「加密笔记」是智能分类，条数按密文列算 —— 就地刷新左栏计数，
        # 否则要点一圈文件夹（重建行）才会发现这篇已经算进去了。
        self._show_info("已加密",
                        "这篇笔记已加密。" +
                        ("\n\n本篇恢复码已生效，忘了口令可用它恢复。"
                         if enabled else
                         "\n\n本篇未启用恢复，忘记口令将无法找回内容。") +
                        "\n\n切换笔记或重新打开程序后，需要输入口令才能查看内容。")

    def change_note_password(self, nid=None):
        n = self._pick_note(nid)
        if not n or not secret.is_blob(n.get("sec")):
            return
        if not self._ensure_unlocked(n, force=True):
            return
        self._sync_editor_to(n)
        pw = self._ask_pw_twice("修改加密口令", "输入新的口令：")
        if not pw:
            return
        blob, key = secret.seal_and_key(
            json.dumps(n.get("content") or [], ensure_ascii=False), pw)
        # 换 salt 等于换 key：旧槽里封的是旧 key，不重封装的话用户手上那张码会
        # 静默失效，直到真忘记口令才发现。每篇独立码下最省事也最诚实的做法是
        # 重新生成一份新码并当场展示，旧码明确作废。
        secret.stamp_hint(blob, self._head_char(n))
        enabled = self._offer_recovery_code(n, blob, key, replacing=True)
        self._apply_new_seal(n, blob, key, pw)
        self._show_info("已更新",
                        "加密口令已更新。" +
                        ("\n\n本篇恢复码已重新生成，旧码已作废。"
                         if enabled else
                         "\n\n本篇未启用恢复，旧恢复码已作废。"))

    def recover_note_with_code(self, n=None):
        """忘记口令时用本篇恢复码取回正文，并**当场重设新口令**。

        关卡：输码 → rid 比对 → 槽 tag 验签 → 主 tag 验签 → 两遍新口令 →
        内存中构造完整新 blob（新 salt / nonce / 新恢复槽）并自检，再一次保存。
        任一步失败都不动原 blob，笔记仍保持原来的加密状态。
        """
        n = n if isinstance(n, dict) else self._pick_note(n)
        if not n or not secret.has_recovery(n.get("sec")):
            self._show_info("无法恢复", "这篇笔记没有设置恢复码，只能用原口令解锁。")
            return False
        sec = n["sec"]
        rc = secret.recovery_of(sec) or {}
        code = self._ask_recovery_code(
            "用恢复码恢复",
            _note_head(n) or secret.hint_of(sec) or "这篇笔记",
            rc.get("rid") or "")
        if not code:
            return False
        try:
            key = secret.open_recovery(sec, code)
            plain = secret.open_with_key(sec, key)   # 取回 key 仍要过主 tag
            content = json.loads(plain)
        except secret.BadPassword as e:
            self._show_error("无法恢复", "%s。未对笔记做任何修改。" % e)
            return False
        except Exception:
            self._show_error("无法恢复", "密文损坏，未对笔记做任何修改。")
            return False
        if not isinstance(content, list):
            content = []
        pw = self._ask_pw_twice(
            "重设加密口令",
            "恢复码已验证。请为这篇笔记设置一个新口令：\n\n"
            "不重设的话磁盘上仍是旧密文，下次打开照样要原口令。")
        if not pw:
            return False
        try:
            blob, new_key = secret.seal_and_key(plain, pw)
            secret.stamp_hint(blob, secret.hint_of(sec))
            new_code = self._offer_recovery_code(n, blob, new_key, replacing=True)
            if secret.open_with_key(blob, new_key) != plain:
                raise ValueError("新密文自检失败")
            if new_code and secret.open_recovery(blob, new_code) != new_key:
                raise ValueError("新恢复槽自检失败")
        except Exception:
            self._show_error("无法恢复", "重设失败，原笔记未做任何修改。")
            return False
        if self.data.get("current_note") != n["id"]:
            self.data["current_note"] = n["id"]
        self._apply_new_seal(n, blob, new_key, pw, content=content)
        self.load_editor()
        self._show_info("已恢复",
                        "这篇笔记已用新口令重新加密，落盘仍是密文。\n\n" +
                        ("新的本篇恢复码已生成，旧码作废。"
                         if new_code else "本篇未启用恢复。"))
        return True

    def add_recovery_code(self, nid=None):
        """给已解锁的加密笔记补建 / 重新生成本篇恢复码（老笔记升级入口）。

        每篇一份码，所以没有「批量补建」：老笔记、当初选了「暂不启用」的笔记，
        都在本篇解锁后单独补。先在副本上建槽，用户取消时原 blob 分毫不动。
        """
        n = self._pick_note(nid)
        if not n or not secret.is_blob(n.get("sec")):
            return
        if not self._ensure_unlocked(n):
            return
        key = self._note_key(n["id"])
        if not key:
            return
        old = secret.has_recovery(n["sec"])
        if old and not self._ask_confirm(
                "重新生成恢复码",
                "程序不保存恢复码，所以无法「查看」旧码，只能换一张新的。\n\n"
                "重新生成后旧码立即作废，确定继续吗？",
                ok_text="重新生成", danger=True):
            return
        blob = dict(n["sec"])
        secret.drop_recovery(blob)
        code = self._offer_recovery_code(n, blob, key, replacing=old)
        if not code:
            return
        n["sec"] = blob
        save_data(self.data)
        self._show_info("恢复码",
                        ("本篇恢复码已重新生成，旧码作废。" if old else
                         "本篇恢复码已生成。") + "请按刚才弹窗的提示另行保管。")

    def clear_note_password(self, nid=None):
        """取消加密：先解出明文（要有口令），再把密文整块删掉。"""
        n = self._pick_note(nid)
        if not n or not secret.is_blob(n.get("sec")):
            return
        if not self._ensure_unlocked(n):
            return
        if not self._ask_confirm("取消加密",
                                 "取消后这篇笔记将以明文保存，不再需要口令。\n"
                                 "确定取消加密吗？", ok_text="取消加密", danger=True):
            return
        self._sync_editor_to(n)
        n.pop("sec", None)
        self._keys.pop(n["id"], None)
        save_data(self.data)
        self.refresh_notes()
        # 同上：取消加密也要就地更新「加密笔记」的条数
        self._refresh_folder_counts()
        if self.data.get("current_note") == n["id"]:
            self.load_editor()
        self._show_info("已取消", "这篇笔记已恢复为明文保存。")

    def unlock_note(self, nid=None):
        """右键「解锁查看」：验口令通过后重新载入编辑器。"""
        n = self._pick_note(nid)
        if not n or not secret.is_blob(n.get("sec")):
            return
        if not self._ensure_unlocked(n, force=True):
            return
        if self.data.get("current_note") != n["id"]:
            self.data["current_note"] = n["id"]
        self.refresh_notes()
        self.load_editor()

    def _seal_prev_note(self, keep=None):
        """切换笔记时把上一篇加密笔记重新封上（内存里不留明文）。

        keep 传「当前要打开的那一篇」：同一篇重载（改字号、置顶之类）不该
        触发重新验口令 —— 用户会以为程序在反复找麻烦。

        返回刚被封上的笔记 id（没封则返回 None）：调用方要拿它去把列表里那一行
        的标题就地刷成遮蔽形态 —— 否则列表还挂着真标题，内容其实已经锁上了。
        """
        nid = getattr(self, "_editor_note", None)
        if not nid or nid == keep:
            return None
        self._editor_note = None
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n or not secret.is_blob(n.get("sec")) or not self._note_key(nid):
            return None
        self._seal_into(n)
        n["content"] = []
        self._keys.pop(nid, None)
        return nid

    def new_note(self):
        f = self.data["current_folder"]
        # 智能分类不能当真实归属：在「星标笔记 / 加密笔记」下新建时若记成 f_star，
        # 这条笔记会不属于任何真实文件夹（只在「全部」里出现），所以一律落到「工作」。
        # 系统区同理：那一格只装内置说明，新笔记进去就再也拖不出来。
        switched = f in SMART_FOLDERS or f == SYSTEM_FOLDER or f == RECYCLE_FOLDER
        folder = self._real_folder_fallback() if switched else f
        import time
        now = time.strftime("%Y-%m-%d", time.localtime())
        n = {"id": uid(), "folder": folder,
             "content": _t(now, ["title"]) + [["t", "\n", []]],
             "pinned": False, "created": time.time() * 1000,
             "updated": time.time() * 1000}
        # 色条颜色在**新建这一刻**就定下来（挑当前用得最少的），之后排序、拖动、
        # 改名都不再变；等界面渲染时再补会让这条笔记的颜色随「当时的用量」漂移。
        n["color"] = pick_note_color(self.data["notes"], folder)
        # ⚠️ 不传 folder：新建的笔记要排在**当前视图**的第一个，而「全部笔记 / 星标」
        # 这类聚合视图是跨文件夹排序的。只跟同文件夹的比，别的文件夹里更小的 order
        # 就会把它压下去 —— 用户看到的就是「新建完出现在列表中间」。
        n["order"] = self.top_note_order(n)
        self.data["notes"].insert(0, n)
        if switched:
            self.data["current_folder"] = folder
            self.refresh_folders()
        # 新建即选中：同时重置多选集合，否则上一条笔记仍留在 _note_sel 里，
        # _restyle_notes 会把它刷成「多选高亮」，看起来就是"旧的没还原、还是选中"。
        self.data["current_note"] = n["id"]
        self._note_sel = {n["id"]}
        self._note_anchor = n["id"]
        save_data(self.data)
        if switched:
            # 智能分类 / 系统区里新建都会切换视图（列表内容整体变了）→ 只能整列重建
            self.refresh_notes()
        else:
            # 普通文件夹：只把新行插到队首，其余笔记原地保留，不再整列重建闪烁
            self._insert_note_row(n)
        # 文件夹行未重建，计数不会自动更新 → 就地刷新（否则新笔记不计入条数）
        self._refresh_folder_counts()
        self._restyle_notes()
        self.load_editor()
        self.text.config(state=tk.NORMAL)
        self.text.focus_set()
        self.text.mark_set("insert", "2.0")

    def is_sample_note(self, n):
        """是否是内置的那篇使用说明（受保护，不允许删除）。

        保护的理由：它是程序里唯一一份上手说明，删掉之后用户只能去翻 README。
        标记由 store 层负责打（新建库在 seed 里带 sample，老库在 load 时按标题补齐），
        所以老数据同样受保护；用户改掉标题就等于收下这篇笔记，标记不再匹配。
        """
        return bool(n and n.get("sample"))

    def is_alarm_index(self, n):
        """是否是「任务提醒列表」这张自动生成的汇总表（固定 id 判定）。"""
        return bool(n and n.get("id") == ALARM_INDEX_ID)

    def is_protected_note(self, n):
        """系统笔记：内置说明 + 任务提醒列表。两者都不能删 / 移 / 改名 / 上锁。

        ⚠️ 判定只认「标记 / 固定 id」，别拿标题比：汇总表的正文是程序每次重写的，
        用户从任何入口都改不动它，标题恒等于常量，但也别给「用户把笔记改名成
        「任务提醒列表」就莫名其妙被锁死」留门。
        """
        return self.is_sample_note(n) or self.is_alarm_index(n)

    def _refuse_sample_delete(self, n=None):
        """删除系统笔记时的统一提示（两类各说各的，别把报表说成使用说明）。"""
        if self.is_alarm_index(n):
            self._show_info("无法删除",
                            "「任务提醒列表」是「任务提醒」分类下的自动汇总表，不能删除、"
                            "不能编辑，也不能移动。\n\n"
                            "它由全库的任务提醒实时生成，要改内容请回到原来的笔记里改闹钟。")
            return
        self._show_info("无法删除",
                        "「%s」是内置的使用说明，既不能删除，也不能编辑。\n\n"
                        "它里面列出了全部快捷键，随时可以回来看。\n"
                        "想写笔记请点右上角「新建笔记」。" % ("欢迎使用" + APP_NAME))

    def delete_note(self, nid=None, forever=False):
        """删除一条笔记（默认进回收站，`forever=True` 则直接销毁）。

        `nid` 给定时删那一条，不给就删编辑器里正打开的那篇 —— 列表右键必须走前者：
        右键点的行**不等于**当前打开的行，早先靠先写 `current_note` 再删，删完编辑器
        会跟着空掉（用户明明在看另一篇，正文却没了）。
        """
        n = self.current_note() if nid is None else next(
            (x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            self._show_info("提示", "请先在中间列表选择一条笔记。")
            return
        if self.is_protected_note(n):
            self._refuse_sample_delete(n)
            return
        if forever:
            ok = self._ask_confirm(
                "彻底删除",
                "确定彻底删除这条笔记？\n\n不会进入回收站，删除后无法还原。",
                ok_text="彻底删除", danger=True)
        else:
            ok = self._ask_confirm(
                "删除", "确定删除这条笔记？\n\n删除后先移入左下角的回收站，%d 天后自动清除。"
                % RECYCLE_TTL_DAYS, ok_text="删除", danger=True)
        if not ok:
            return
        nid = n["id"]
        prev = self.data.get("current_note")
        # 删之前记一份列表顺序：删的是正在看的那篇时，编辑器要顺到「顶替它位置」
        # 的那一条（标签条里没有它就退回列表里的下一条，与文件管理器同款手感）。
        order = [x["id"] for x in self.visible_notes()]
        # 快照先落回收站，再从正式列表移除：顺序反了就再也捞不回来了。
        # 彻底删除相反 —— 连快照都不留，直接跳过这一步。
        if not forever:
            store().recycle_put("note", dict(n), origin=n.get("folder"))
        self.data["notes"] = [x for x in self.data["notes"] if x["id"] != nid]
        # 标签一并收掉（_render_tabs 也会剪掉库里已经没有的 id，这里先剪是为了
        # 算出「下一个是谁」）
        nxt = self._tab_drop(nid)
        if prev == nid:
            if not nxt and nid in order:
                rest = [i for i in order if i != nid]
                i = order.index(nid)
                nxt = rest[i] if i < len(rest) else (rest[-1] if rest else None)
            self.data["current_note"] = nxt
        self._note_sel.discard(nid)
        if self._note_anchor == nid:
            self._note_anchor = None
        # 删的是别的篇时，列表高亮跟着编辑器走：否则正文还开着这一篇，行上却是空的
        keep = self.data.get("current_note")
        if prev != nid and keep and any(x["id"] == keep for x in self.visible_notes()):
            self._note_sel = {keep}
            self._note_anchor = keep
        save_data(self.data)
        # 只移除这一行 + 就地刷新计数：文件夹行与其余笔记都不重建 → 不闪烁
        if nid in self._rows:
            self._remove_note_rows([nid])
        else:
            self.refresh_notes()
        self._refresh_folder_counts()
        # 删除是「只移除行、不重建整列」，走不到 refresh_notes 里那条回收站刷新，
        # 徽标必须在这里补刷 —— 否则要等用户点一下回收站（recycle_open）才变。
        self._restyle_recycle_bar()
        if prev == nid:
            self._active_title = self._active_preview = self._active_date = None
            self._restyle_notes()
            self.load_editor()
        else:
            # 正文没换人：只重画样式 + 标签条，别整篇重装（那会把光标与滚动位置
            # 一起弹回开头，看着像「删了别篇、自己这页也跳了一下」）。
            self._restyle_notes()
            self._render_tabs()

    # ---------- 回收站（左下角入口） ----------
    def _recycle_view(self):
        return self.data.get("current_folder") == RECYCLE_FOLDER

    def _real_folder_fallback(self):
        """还原笔记时的归属退路：优先「工作」，没有就取第一格普通文件夹。"""
        real = [f["id"] for f in self.data["folders"]
                if f["id"] not in SMART_FOLDERS and f["id"] != SYSTEM_FOLDER]
        return "f_work" if "f_work" in real else (real[0] if real else "f_work")

    def recycle_open(self):
        """切到回收站视图：只展示快照，任何笔记都不许打开（见 _load_editor_body）。"""
        purged = store().recycle_purge()
        if purged:
            # 过期快照对应的图片没人引用了，回收掉（启动时那条无条件 GC 覆盖不到
            # 运行中到期的项）
            self._img_gc(resave=False)
        self.data["current_folder"] = RECYCLE_FOLDER
        self.data["current_note"] = None
        self._note_sel = set()
        self._note_anchor = None
        # 左栏多选/高亮必须跟着收掉：`_restyle_folders` 除了认 current_folder，
        # 还会把 `_folder_sel` 里的格子点亮，所以只改 current_folder 会留下一格
        # 绿底 —— 看着像「文件夹和回收站同时选中」。视图已经换了，多选也没意义。
        self._folder_sel = set()
        self._folder_anchor = None
        self._restyle_folders()
        # 搜索走的是 notes 表的 SQL，回收站里的假笔记不在表里 —— 清空搜索框，
        # 免得用户搜了半天一条都不出来、以为是回收站坏了。
        # ⚠️ 必须静默清：StringVar 的 write trace 不管值变没变都会触发，
        # 空搜索框 set("") 也会跑一次 refresh_notes —— 加上下面自己那次，
        # 列表就整列重建两遍（用户看到闪两次）。
        try:
            self._search_quiet = True
            try:
                self.search_var.set("")
            finally:
                self._search_quiet = False
        except Exception:
            pass
        self.refresh_notes()
        self.load_editor()
        self._restyle_recycle_bar()
        if purged:
            self._after(0, lambda: self._show_info(
                "回收站", "已自动清除 %d 条超过 %d 天的项目。" % (purged, RECYCLE_TTL_DAYS)))

    def recycle_refresh_if_open(self):
        """定时 / 数据变化后刷新：徽标总是跟上条数，回收站视图开着才整列重画。

        徽标那一下不区分视图 —— 过期项被 `recycle_purge` 清掉时用户多半正看着
        别处，只刷「打开的回收站」会让左下角那个数字一直挂着旧值（与删除后
        不刷是同一类毛病）。
        """
        self._restyle_recycle_bar()
        if self._recycle_view():
            self.recycle_open()

    def _restore_note_snapshot(self, snap):
        n = dict(snap or {})
        if not n.get("id"):
            return
        have = {x["id"] for x in self.data["notes"]}
        if n["id"] in have:                     # id 撞车（极少见）→ 给个新 id
            n["id"] = uid()
        fids = {f["id"] for f in self.data["folders"]}
        if n.get("folder") not in fids or n.get("folder") in SMART_FOLDERS:
            n["folder"] = self._real_folder_fallback()
        n["order"] = self.top_note_order(n, n["folder"])
        self.data["notes"].append(n)
        return n

    def _restore_folder_snapshot(self, snap):
        fols = [dict(f) for f in (snap or {}).get("folders") or [] if isinstance(f, dict)]
        if not fols:
            return
        have_f = {f["id"] for f in self.data["folders"]}
        idmap = {}
        added = []
        for f in fols:
            nf = dict(f)
            if nf.get("id") in have_f:
                new = uid()
                idmap[nf["id"]] = new
                nf["id"] = new
            have_f.add(nf["id"])
            added.append(nf)
        self.data["folders"].extend(added)
        real_f = {f["id"] for f in self.data["folders"]}
        for f in added:
            pid = f.get("parent")
            if pid in idmap:
                f["parent"] = idmap[pid]
            elif pid not in real_f or pid == f["id"]:
                f.pop("parent", None)            # 父级已不存在 → 提到根层
        have_n = {n["id"] for n in self.data["notes"]}
        for old in (snap or {}).get("notes") or []:
            if not isinstance(old, dict):
                continue
            n = dict(old)
            if n.get("id") in have_n:
                n["id"] = uid()
            have_n.add(n["id"])
            if n.get("folder") in idmap:
                n["folder"] = idmap[n["folder"]]
            if n.get("folder") not in real_f:
                n["folder"] = self._real_folder_fallback()
            n["order"] = self.top_note_order(n, n["folder"])
            self.data["notes"].append(n)

    def recycle_restore(self, rid=None, restore_all=False):
        """还原：单个 / 当前选中的 / 全部。文件夹先还原，笔记后还原（找回归属）。"""
        recs = store().recycle_all()
        if rid:
            targets = [r for r in recs if r["id"] == rid]
        elif restore_all:
            targets = recs
        else:
            sel = set(self._note_sel)
            targets = [r for r in recs if r["id"] in sel]
        if not targets:
            self._show_info("提示", "没有可还原的项目。")
            return
        targets.sort(key=lambda r: 0 if r.get("kind") == "folder" else 1)
        restored = []
        for rec in targets:
            snap = rec.get("payload") or {}
            if rec.get("kind") == "folder":
                self._restore_folder_snapshot(snap)
            else:
                self._restore_note_snapshot(snap)
            store().recycle_remove(rec["id"])
            restored.append(rec)
        save_data(self.data)
        self._note_sel = set()
        self._note_anchor = None
        self.refresh_all()
        self._restyle_recycle_bar()
        self._show_info("已还原", "已还原 %d 项。" % len(restored))

    def recycle_clear(self):
        if not store().recycle_count():
            self._show_info("提示", "回收站已经是空的。")
            return
        cnt = store().recycle_count()
        if not self._ask_confirm(
                "清空回收站",
                "回收站里有 %d 项，清空后将无法恢复，是否继续？" % cnt,
                ok_text="清空", danger=True):
            return
        store().recycle_clear()
        # 快照没了，只被回收站笔记引用的图片就成了孤儿：顺手回收 media 文件。
        # resave=False —— 回收站是独立表，没动 self.data，不必再写一次库。
        self._img_gc(resave=False)
        self._note_sel = set()
        self._note_anchor = None
        self.refresh_notes()
        self.load_editor()
        self._restyle_recycle_bar()

    def recycle_purge_item(self, rid):
        """清空回收站里的**这一条**（笔记或文件夹），彻底删除、不可还原。

        与 `recycle_clear` 的区别只在范围：那条是整站清空，这条只动右键点中的
        记录，其余项目原样留着（连同各自的 7 天倒计时）。
        """
        rec = store().recycle_get(rid)
        if not rec:
            # 并发场景：另一个窗口/定时清理已经把它带走了，安静收尾即可。
            self._note_sel.discard(rid)
            self.refresh_notes()
            self._restyle_recycle_bar()
            return
        kind = "文件夹" if rec.get("kind") == "folder" else "笔记"
        # 标题走 recycle_item_view：文件夹那条是「文件夹 · 名字」，笔记那条是首行，
        # 与列表里看到的字面一致 —— 确认框里别自己拼一份不一样的。
        title = self.trunc(self.title_of(recycle_item_view(rec)), 24)
        if not self._ask_confirm(
                "清空%s" % kind,
                "「%s」将被彻底删除，无法还原，是否继续？" % title,
                ok_text="清空", danger=True):
            return
        store().recycle_remove(rid)
        # 这条快照对应的图片没人引用了，回收它（整站清空的同款收尾）
        self._img_gc(resave=False)
        self._note_sel.discard(rid)
        if self._note_anchor == rid:
            self._note_anchor = None
        self.refresh_notes()
        self.load_editor()
        self._restyle_recycle_bar()

    def recycle_purge_items(self, rids):
        """批量清空回收站里的若干条（多选后右键）。文件夹与笔记混选一起处理。

        与 `recycle_purge_item` 同一套收尾：确认 → 逐条 `recycle_remove` →
        清选中 → 重画列表 / 正文提示 / 徽标。只影响被选中的记录，别的项目
        （连同各自的 7 天倒计时）原样留着。
        """
        ids = list(dict.fromkeys(rids or []))       # 去重且保序，确认框数字才准
        recs = [r for r in (store().recycle_get(i) for i in ids) if r]
        if not recs:
            # 全被定时清理/别的窗口带走了：安静收口，别弹一句「清空 0 项」。
            self._note_sel = set()
            self._note_anchor = None
            self.refresh_notes()
            self.load_editor()
            self._restyle_recycle_bar()
            return
        folders = sum(1 for r in recs if r.get("kind") == "folder")
        notes = len(recs) - folders
        msg = "选中的 %d 项将被彻底删除，无法还原，是否继续？" % len(recs)
        if folders and notes:
            # 混选时点明构成：清掉一个文件夹等于连它里面的笔记一起没了，
            # 只报「3 项」会让人以为只是三条笔记。
            msg = ("选中的 %d 项（%d 个文件夹、%d 条笔记）将被彻底删除，"
                   "无法还原，是否继续？" % (len(recs), folders, notes))
        if not self._ask_confirm(
                "清空选中的 %d 项" % len(recs), msg,
                ok_text="清空", danger=True):
            return
        for rec in recs:
            store().recycle_remove(rec["id"])
            self._note_sel.discard(rec["id"])
            if self._note_anchor == rec["id"]:
                self._note_anchor = None
        # 这批快照引用的图片一并回收（整站 / 单条清空的同款收尾）
        self._img_gc(resave=False)
        self.refresh_notes()
        self.load_editor()
        self._restyle_recycle_bar()

    def toggle_pin(self):
        n = self.current_note()
        if not n:
            return
        n["pinned"] = not n["pinned"]
        n["updated"] = __import__("time").time() * 1000
        n["order"] = self.top_note_order(n)
        save_data(self.data)
        self.refresh_notes()
        self.load_editor()

    def toggle_star(self, nid=None):
        n = self.current_note() if nid is None else next(
            (x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        n["starred"] = not n.get("starred", False)
        n["order"] = self.top_note_order(n)
        save_data(self.data)
        # 就地更新文件夹计数（不重建文件夹行 → 不闪烁）
        self._refresh_folder_counts()
        # 在“星标笔记”文件夹下取消星标 → 该条需移出列表，必须重建
        if self.data["current_folder"] == "f_star" and not n["starred"]:
            self.refresh_notes()
            return
        # 其余情况：只就地更新那一行星标按钮的图标，避免整列重建导致闪烁。
        # 走 _paint_star（唯一一处刷星标的地方），它会顺手把 rec["starred"] 这份
        # 快照更新掉 —— 不更新的话，下一次 _restyle_notes（切换笔记就会触发）
        # 会照着旧快照把刚点亮的星标刷回灰色。
        rec = self._rows.get(n["id"])
        if rec:
            self._paint_star(n["id"], rec, rec["row"]._base_bg)

    def new_folder(self, parent=None):
        """新建文件夹 / 子文件夹（parent 为父 id；None = 根层）。

        插位置的讲究：写到「父这一支的最后」而不是整个列表末尾 —— 父若有兄弟，
        新行会出现在它自己那棵子树里，界面上的相对位置和层级一致；虽然渲染按
        parent 重排过，但导出/备份里数组顺序就是用户看到的顺序，别让它乱跳。
        """
        pname = ""
        if parent:
            pf = next((x for x in self.data["folders"] if x["id"] == parent), None)
            if not pf or pf.get("smart"):
                self._show_info("提示", "只能在普通文件夹里建子文件夹。")
                return
            if parent == SYSTEM_FOLDER:
                self._show_info("提示", "「系统」文件夹是程序自带的，不能在它下面建子文件夹。")
                return
            # 层数上限：往下再深一层就缩进到看不见了
            if folder_depth(parent, self.data["folders"]) + 1 > MAX_FOLDER_DEPTH:
                self._show_info("提示", "文件夹最多 %d 层，不能再往下建了。"
                                % MAX_FOLDER_DEPTH)
                return
            pname = pf["name"]
        name = self._ask_text(("在「%s」下新建子文件夹" % pname) if pname else "新建文件夹",
                              "文件夹名称：", "新文件夹")
        if not name:
            return
        f = {"id": uid(), "name": name.strip()}
        if parent:
            f["parent"] = parent
            # 父收着的话新文件夹会藏在里面看不见（像是没建成），建完顺手展开
            pf.pop("collapsed", None)
            # 落在父/最后一个兄弟之后
            kin = set(subtree_ids(parent, self.data["folders"]))
            last = max((i for i, x in enumerate(self.data["folders"])
                        if x["id"] in kin or x["id"] == parent), default=-1)
            self.data["folders"].insert(last + 1, f)
        else:
            # 根层新文件夹排在所有根层真实文件夹之后。「系统」钉在第一个真实文件夹
            # 位上（见 store._ensure_system_folder），直接 append 或插在它前面都会
            # 让系统那一格跑来跑去。
            last = -1
            for i, x in enumerate(self.data["folders"]):
                if (x["id"] not in SMART_FOLDERS and x["id"] != SYSTEM_FOLDER
                        and not x.get("parent")):
                    last = i
            if last < 0:                    # 还没有自建文件夹：挨着「系统」往后放
                last = next((i for i, x in enumerate(self.data["folders"])
                             if x["id"] == SYSTEM_FOLDER), len(self.data["folders"]) - 1)
            self.data["folders"].insert(last + 1, f)
        save_data(self.data)
        # 就地插这一行，别重建整列：整列重建 = 所有行销毁再建一遍（控件全换新的），
        # 看上去就是「新建完一个，别的文件夹全跳一下」。父行长出三角那点事交给
        # _restyle_folders（它会在三角还没绑事件时补上）。
        self._folder_sel = {f["id"]}
        self._folder_anchor = f["id"]
        if not self._insert_new_folder_row(f):     # 兜底：拿不到行才退回整列重建
            self.refresh_folders(keep_scroll=True)
            self._restyle_folders()

    def _twisty_click(self, fid):
        """点行首三角：只收/放这一支，不选中文件夹。

        返回 "break" 截断同一画布上后续注册的绑定 —— 不截断的话 _folder_click
        会接着跑，点一下三角顺便把当前文件夹切走了。
        """
        self._toggle_folder(fid)
        return "break"

    def _folder_double_click(self, fid):
        """双击文件夹行 = 收起 / 展开这一支（有子文件夹才生效）。"""
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if f and not f.get("smart") and folder_children(fid, self.data["folders"]):
            self._toggle_folder(fid)

    def _toggle_folder(self, fid):
        """收起 / 展开这一支（点击行首的三角）。"""
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f or f.get("smart") or not folder_children(fid, self.data["folders"]):
            return
        if f.get("collapsed"):
            f.pop("collapsed", None)
        else:
            f["collapsed"] = True
        save_data(self.data)
        # 就地增删这一支的行，**不重建整列**：整列重建要把所有行销毁再建一遍，
        # 控件全换成新的，看上去就是「别的文件夹跟着跳一下」。
        if f.get("collapsed"):
            ok = self._remove_folder_rows(fid)
        else:
            ok = self._insert_folder_rows(fid)
        if not ok:                 # 兜底：拿不到对应行（极端时序）时退回整列重建
            self.refresh_folders(keep_scroll=True)

    # ---------- 整列级：全部展开 / 全部收起（「文件夹」标题右侧那两枚） ----------
    def expand_all_folders(self):
        """全部展开：把所有真实文件夹的收起标记清掉（智能分类不参与）。"""
        self._set_all_folders_collapsed(False)

    def collapse_all_folders(self):
        """全部收起：把所有**有子文件夹**的真实文件夹收起来。"""
        self._set_all_folders_collapsed(True)

    def _set_all_folders_collapsed(self, collapsed):
        """整列收 / 放。纯逻辑（该标哪些、叶子不标）在 store.set_all_collapsed 里，
        那里有测试盯着；这里只管落库 + 把左栏同步过去。

        ⚠️ **不许走 refresh_folders**：那是「全销毁 + 全重建」，中间隔一次 idle，
        列表会空一瞬 —— 用户看到的就是「点一下白闪一下」（单个文件夹收放早就
        避开了这条，整列收放一直没跟上）。`_sync_folder_rows` 只增删该变的行。
        状态没变时 store 那边直接返回 False，连这一趟都不跑。
        """
        if not set_all_collapsed(self.data["folders"], collapsed):
            return
        save_data(self.data)
        if not self._sync_folder_rows():        # 兜底：拿不到行序才退回整列重建
            self.refresh_folders(keep_scroll=True)

    def del_folder(self, fid=None, forever=False):
        """删除文件夹（默认连同上 snapshots 进回收站；`forever=True` 直接销毁）。"""
        target = fid or self.data["current_folder"]
        f = next((x for x in self.data["folders"] if x["id"] == target), None)
        if not f or f.get("smart"):
            self._show_info("提示", "「全部笔记 / 星标笔记 / 加密笔记」是智能分类，不能删除。")
            return
        if fid == SYSTEM_FOLDER:
            self._show_info("提示", "「系统」文件夹是程序自带的，不能删除。")
            return
        # 删的是一整棵子树：子文件夹及其里的笔记一起删。留着的话它们变成没有父的
        # 孤儿，只在 normalize_folder_parents 里被提到根层 —— 用户眼里就是
        # 「删了一个，边上多出来几个」。
        tree = set(subtree_ids(f["id"], self.data["folders"]))
        subs = len(tree) - 1
        cnt = sum(1 for n in self.data["notes"] if n.get("folder") in tree)
        # 提示固定两行：名称按可用宽度截断（过长用省略号），避免折出第三行
        try:
            fnt = tkfont.Font(family=FONT[0], size=12)
            def fit(head, tail):
                return self._ellipsize(
                    f["name"], DLG_TEXT_W - fnt.measure(head) - fnt.measure(tail), fnt)[0]
        except Exception:
            fnt = None
            def fit(head, tail):
                return f["name"]
        if cnt:
            head = "文件夹「"
            tail = "」下有 %d 条笔记" % cnt
            if subs:
                tail += "（含 %d 个子文件夹）" % subs
            tail += "。"
            msg = "%s%s%s\n连同笔记一起删除，是否继续？" % (head, fit(head, tail), tail)
        elif subs:
            head, tail = "文件夹「", "」下还有 %d 个子文件夹。" % subs
            msg = "%s%s%s\n一起删除，是否继续？" % (head, fit(head, tail), tail)
        else:
            head, tail = "确定删除文件夹「", "」？"
            msg = "%s%s%s" % (head, fit(head, tail), tail)
        if forever:
            msg += "\n\n不会进入回收站，删除后无法还原。"
        else:
            msg += "\n\n删除后先移入回收站，%d 天后自动清除。" % RECYCLE_TTL_DAYS
        if not self._ask_confirm("彻底删除文件夹" if forever else "删除文件夹", msg,
                                 ok_text="彻底删除" if forever else "删除",
                                 danger=True):
            return
        # 整棵子树 + 里面的笔记一起快照：还原时要把层级和归属一并带回来，
        # 只存文件夹名的话还原出来就是一堆空壳（笔记还在回收站里找不到家）。
        # 彻底删除连快照都不留。
        if not forever:
            store().recycle_put(
                "folder",
                {"folders": [dict(f) for f in self.data["folders"] if f["id"] in tree],
                 "notes": [dict(n) for n in self.data["notes"]
                           if n.get("folder") in tree]},
                origin=f["id"])
        if cnt:
            dropped = {n["id"] for n in self.data["notes"] if n.get("folder") in tree}
            self.data["notes"] = [x for x in self.data["notes"] if x["id"] not in dropped]
            self._note_sel -= dropped
            if self.data.get("current_note") in dropped:
                # 当前打开的那篇也在被删的文件夹里：标签一并收掉，编辑器顺到邻居
                self.data["current_note"] = self._tab_drop(self.data["current_note"])
        self.data["folders"] = [x for x in self.data["folders"] if x["id"] not in tree]
        if self.data.get("current_folder") in tree:
            self.data["current_folder"] = "f_all"
        save_data(self.data)
        self.refresh_all()

    def rename_folder(self, fid):
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        if fid == SYSTEM_FOLDER:
            self._show_info("提示", "「系统」文件夹的名字是程序固定的，不能改。")
            return
        if fid in SMART_FOLDERS:
            # 智能分类（含「任务提醒」）是程序固定的视图，名字与落库 id 一一对应。
            # 右键菜单已经置灰，这里再兜一道：双击重命名 / 快捷键等别的入口同样拦住。
            self._show_info("提示", "智能分类是程序固定的视图，名字不能改，也不能移动。")
            return
        name = self._ask_text("重命名文件夹", "文件夹名称：", f["name"])
        if not name:
            return
        f["name"] = name.strip()
        save_data(self.data)
        # 就地改该行文字，不整列重建 → 不闪烁
        row = self._folder_rows.get(fid)
        lbl = self._folder_name_labels.get(fid)
        if row is not None and lbl is not None and lbl.winfo_exists():
            row._full_name = f["name"]
            row._name_clipped = False
            lbl.set_text(f["name"], 0)
            self._fit_folder_name(fid)      # 新名字重新按像素截断
        else:
            self.refresh_folders()

    def _folder_blank_popup(self, event):
        """文件夹列**空白处**的右键菜单 —— 底部「新建文件夹 / 删除文件夹」按钮撤掉后，
        这里是建文件夹的主要入口（行上的右键仍弹该文件夹自己的菜单）。

        只放「新建文件夹」：空白处没有对象可指，重命名 / 删除 / 移动都得先选中某一项，
        那些动作留在行菜单里；在这里给它们只会让人怀疑「到底删哪个」。
        """
        m = PopupMenu(self.root, self.pal)
        m.add("新建文件夹", icon="folder", command=lambda: self.new_folder())
        m.popup(event.x_root, event.y_root)
        return "break"

    def _note_blank_popup(self, event):
        """笔记列表**空白处**的右键菜单：只给「新建笔记」。

        空白处没有对象可指 —— 置顶 / 星标 / 移动到文件夹 / 加密都得先选中某一条，
        那些动作留在行菜单里（`_popup`）。这里只放「再写一篇」这个入口，与文件夹列
        空白菜单（`_folder_blank_popup`）同一套规矩：菜单里凭空多出几项、而用户又
        没选中任何东西时，最容易误删 / 误移。

        回收站视图不弹：那儿的条目是回收记录，`new_note` 会把归属改到工作文件夹并
        整体切走视图 —— 右键一块空白却「凭空换页」，比没有菜单更让人意外。
        """
        if self._recycle_view():
            return "break"
        m = PopupMenu(self.root, self.pal)
        m.add("新建笔记", icon="pencil", command=self.new_note)
        m.popup(event.x_root, event.y_root)
        return "break"

    def _folder_popup(self, event, fid):
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        # 右键时若该项不在当前多选集合中，则以它为唯一选中
        if fid not in self._folder_sel:
            self._folder_sel = {fid}
            self._folder_anchor = fid
            self._restyle_folders()
        m = PopupMenu(self.root, self.pal)
        if len(self._folder_sel) > 1:
            # 多选里可能混着智能分类 / 系统区（Shift 区间很容易顺手带上）。
            # 计数只算真能删的那些 —— 否则承诺「删除 3 个」最后只少了 1 个。
            deletable = [i for i in self._folder_sel
                         if i not in SMART_FOLDERS and i != SYSTEM_FOLDER]
            m.add("删除选中的 %d 个文件夹" % len(deletable), icon="trash",
                  danger=True, enabled=bool(deletable),
                  command=lambda: self._delete_folders(list(self._folder_sel)))
        elif fid == SYSTEM_FOLDER:
            # 系统区是程序自带的一格：三项都列出来、全部置灰。菜单里凭空少几行
            # 会让人以为「是不是没点到」，写明原因反而省一次尝试。
            m.add("重命名（系统文件夹不可改）", icon="pencil", enabled=False)
            m.add("新建子文件夹（系统文件夹下不能建）", icon="folder", enabled=False)
            m.add("移动笔记到…（系统文件夹不可移）", icon="folder", enabled=False)
            m.add_separator()
            m.add("删除文件夹（系统文件夹不可删）", icon="trash", enabled=False)
        else:
            if f.get("smart"):
                # 智能分类四项全列出来、全置灰并写明原因：菜单里凭空少几行，
                # 用户会以为「是不是没点到」。「任务提醒」同样受这一套保护。
                m.add("重命名（智能分类不可改）", icon="pencil", enabled=False)
                m.add("新建子文件夹（智能分类下不能建）", icon="folder", enabled=False)
                m.add("移动笔记到…（智能分类不可整体移动）", icon="folder", enabled=False)
                m.add_separator()
                m.add("删除文件夹（智能分类不可删）", icon="trash", enabled=False)
            else:
                m.add("重命名", icon="pencil", command=lambda: self.rename_folder(fid))
                # 「新建子文件夹」放在最前：树里最常见的下意识动作就是「在这儿再开一层」
                too_deep = folder_depth(fid, self.data["folders"]) + 1 > MAX_FOLDER_DEPTH
                m.add("新建子文件夹" if not too_deep else
                      "新建子文件夹（已达 %d 层上限）" % MAX_FOLDER_DEPTH,
                      icon="folder", enabled=not too_deep,
                      command=lambda: self.new_folder(parent=fid))
                # 有子文件夹才谈得上收/放；标签跟着当前状态翻面
                if folder_children(fid, self.data["folders"]):
                    m.add("展开子文件夹" if f.get("collapsed") else "收起子文件夹",
                          icon="panel", command=lambda: self._toggle_folder(fid))
                m.add("移动笔记到…", icon="folder",
                      command=lambda: self._move_notes_out(fid))
                m.add_separator()
                m.add("删除文件夹", icon="trash", danger=True,
                      command=lambda: self.del_folder(fid))
        m.popup(event.x_root, event.y_root)

    def _move_notes_out(self, fid):
        """右键 → 移动笔记：把该文件夹下的笔记全部移到另一个文件夹。"""
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        if f.get("smart"):
            self._show_info("提示", "智能分类是筛选出来的视图，不能整体移动。")
            return
        if fid == SYSTEM_FOLDER:
            self._show_info("提示", "「系统」文件夹只放内置说明，里面的笔记不能移出。")
            return
        notes = self.folder_notes(fid)
        if not notes:
            self._show_info("提示", "文件夹「%s」下没有笔记。" % f["name"])
            return
        # 自己的子文件夹不算目标：挪进子层之后父视图照样能看见它们（父级含子树计数），
        # 看起来像「什么都没动」，但笔记的归属确实变了 —— 这种操作不给人做。
        # 系统区同样排除：它只装说明笔记，别人的笔记挪进去等于锁进一个动不了的角落。
        kin = set(subtree_ids(fid, self.data["folders"]))
        targets = [x for x in self.data["folders"]
                   if not x.get("smart") and x["id"] not in kin
                   and x["id"] != SYSTEM_FOLDER]
        if not targets:
            self._show_info("提示", "没有其他文件夹可以移入。")
            return
        tid = self._ask_choose_folder(
            "移动笔记",
            "把「%s」下的 %d 条笔记全部移动到：" % (f["name"], len(notes)),
            targets)
        if not tid:
            return
        now = __import__("time").time() * 1000
        for n in notes:
            n["folder"] = tid
            n["updated"] = now
        # 正在看源文件夹时，移走的条目已不在列表里 → 清掉选中，避免高亮指向看不见的条目；
        # 在其它文件夹（含"全部"）下这些条目仍可见，保持原选中不动。
        if self.data.get("current_folder") == fid:
            self.data["current_note"] = None
        save_data(self.data)
        self.refresh_all()
