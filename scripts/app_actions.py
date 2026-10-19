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

from store import (APP_NAME, uid, _t, seed, store, save_data,
                   normalize_import, merge_import, SMART_FOLDERS,
                   folder_children, folder_depth, folder_ancestors,
                   subtree_ids, MAX_FOLDER_DEPTH)
from theme import FONT
from widgets import (CodeBlock, HRule, PopupMenu,
                     DLG_TEXT_W, SAVE_DEBOUNCE_MS, ROW_REFRESH_DEBOUNCE_MS,
                     _png_icon_photo, _popover_bg, _popover_border, _mix_hex)
from md_conv import (detect_prefix, find_urls, find_time_call, is_hr, parse_fence,
                     MD_LINK_RE,
                     LI_INDENT, BULLET_MARK, NUM_MARK, TRI_MARK, split_list_prefix,
                     TODO_OFF, TODO_LEGACY, TODO_MARKS, TODO_GAP, TODO_GAP_TAG, _MARK_NBSP)
import exporter
import pdf_export
import secret
import mention
import calc
import alarm
from mention import find_mention, note_url, note_id_of_url, mention_text, rank
from mention_ui import MentionPopup


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



class ActionsMixin(object):
    def export_data(self):
        """把文件夹 + 笔记 + 设置导出成一份 JSON 备份。"""
        import datetime
        p = self.pal
        default = "%s备份_%s.json" % (APP_NAME, datetime.datetime.now().strftime("%Y%m%d-%H%M"))
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出备份", defaultextension=".json",
            initialfile=default, initialdir=os.path.expanduser("~"),
            filetypes=[("JSON 备份文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        snap = {
            "_app": "notes-app",
            "_version": 1,
            "_exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "folders": self.data["folders"],
            "notes": self.data["notes"],
            "settings": self.data.get("settings", {}),
            "styles": self.data.get("styles", {}),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._show_error("导出失败", "写入文件失败：\n%s" % e)
            return
        self._show_info("导出完成",
                        "已导出 %d 个文件夹、%d 条笔记\n%s" %
                        (len(self.data["folders"]), len(self.data["notes"]), path))

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
        """从 JSON 备份导入：可选「覆盖现有数据」或「合并追加」。"""
        path = filedialog.askopenfilename(
            parent=self.root, title="选择备份文件", initialdir=os.path.expanduser("~"),
            filetypes=[("JSON 备份文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            self._show_error("导入失败", "文件无法解析为 JSON：\n%s" % e)
            return
        if not isinstance(d, dict) or not isinstance(d.get("folders"), list) \
                or not isinstance(d.get("notes"), list):
            self._show_error("导入失败",
                             "不是有效的笔记备份文件（缺少 folders / notes 列表）。")
            return
        mode = self._ask_import_mode(len(d["folders"]), len(d["notes"]),
                                     d.get("_exported_at") or "")
        if not mode:
            return
        new = (normalize_import(d, self.data.get("settings", {}),
                                self.data.get("styles", {})) if mode == "overwrite"
               else merge_import(d, self.data))
        store().reset(new)
        self._apply_new_data(new)
        self._show_info("导入完成",
                        "导入方式：%s\n现有 %d 个文件夹、%d 条笔记" %
                        ("覆盖" if mode == "overwrite" else "合并",
                         len(new["folders"]), len(new["notes"])))

    def clear_all_data(self):
        """清空所有数据并回到初次打开的状态（4 个文件夹 + 示例笔记）。"""
        mine = [f for f in self.data["folders"] if not f.get("smart")]
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
                "程序会回到初次打开时的状态（4 个文件夹）。\n\n"
                "确认清空？",
                ok_text="确认清空", danger=True):
            return
        new = seed()
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
        n["folder"] = fid
        n["updated"] = __import__("time").time() * 1000
        save_data(self.data)
        # 就地刷新列表与所有文件夹计数（不重建文件夹行 → 不闪烁）
        self.refresh_notes()
        for f in self.data["folders"]:
            lbl = self._folder_count_labels.get(f["id"])
            if lbl:
                lbl.configure(text=str(len(self.folder_notes(f["id"]))))

    def _popup(self, event, nid):
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
            only_sample = bool(picked) and all(self.is_sample_note(x) for x in picked)
            m.add("删除选中的 %d 条笔记" % len(self._note_sel), icon="trash",
                  danger=True, enabled=not only_sample,
                  command=lambda: self._delete_notes(list(self._note_sel)))
            m.popup(event.x_root, event.y_root)
            return
        m.add("置顶" if not n["pinned"] else "取消置顶", icon="pin",
              command=lambda: self._popup_pin(nid))
        m.add("星标" if not n.get("starred", False) else "取消星标", icon="star",
              command=lambda: self.toggle_star(nid))
        goto = self._goto_item(n)
        if goto:
            m.add(goto[0], icon="folder", enabled=goto[1],
                  command=lambda: self._popup_goto_folder(nid))
        if not self.is_sample_note(n):
            sealed = secret.is_blob(n.get("sec"))
            if sealed:
                unlocked = bool(self._note_key(nid) and n.get("content"))
                if unlocked:
                    m.add("修改加密口令…", icon="lock",
                          command=lambda: self.change_note_password(nid))
                    m.add("取消加密…", icon="unlock",
                          command=lambda: self.clear_note_password(nid))
                else:
                    m.add("输入口令解锁…", icon="lock",
                          command=lambda: self.unlock_note(nid))
            else:
                m.add("设置加密口令…", icon="lock",
                      command=lambda: self.set_note_password(nid))
        m.add_separator()
        sample = self.is_sample_note(n)
        m.add("删除笔记（内置示例，不可删）" if sample else "删除笔记",
              icon="trash", danger=True, enabled=not sample,
              command=lambda: self._popup_delete(nid))
        m.popup(event.x_root, event.y_root)

    def _popup_pin(self, nid):
        self.data["current_note"] = nid
        self.toggle_pin()

    def _popup_delete(self, nid):
        self.data["current_note"] = nid
        self.delete_note()

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
            pop = MentionPopup(self.root, self.pal, on_pick=self._mention_pick)
            self._mention_pop = pop
            self._mention_start = hit[0]
            x, y = self._mention_anchor_xy()
            pop.open(self._mention_candidates(""), x, y)
            return
        self._mention_start = hit[0]
        pop.set_items(self._mention_candidates(hit[1]))

    def _mention_recheck(self, event=None):
        """光标动了（方向键 / 滚轮）：推迟到 idle 再核对 —— 实例绑定先于类绑定，
        现在核对的话光标还没被 Home / End 挪走，核对等于没核。"""
        if getattr(self, "_mention_pop", None) is None:
            return None
        self._after_idle(self._mention_refilter)
        return None

    def _mention_refilter(self):
        """正文又变了一个字：重算候选（走 <<Modified>>，中文输入也能跟得上）。"""
        pop = getattr(self, "_mention_pop", None)
        if pop is None or not pop.is_open():
            return
        hit = self._mention_at()
        if hit is None:
            self._mention_close()
            return
        self._mention_start = hit[0]
        pop.set_items(self._mention_candidates(hit[1]))

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
        save_data(self.data)

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
        """
        tag = str(tag)
        return (tag.startswith(self.LI_TAG_PREFIX)
                or tag in ("find", "locked_ph", "colsel"))

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
                # 正文里的小闹钟。dump 只给出图片名，数据在内存表里 —— 名字对不上
                # （比如历史遗留的图）就跳过，等于断掉那个不明来源的图元
                d = self._alarms_get().get(value)
                if d:
                    out.append(["alarm", {"id": d.get("id"), "text": d.get("text"),
                                          "at": d.get("at")}])
            elif key == "window":
                w = self.text.nametowidget(value)
                if isinstance(w, CodeBlock):
                    out.append(["code", w.lang, w.snapshot()])
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

    def insert_bullet(self):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        line = self.text.index("insert").split(".")[0]
        if self._title_line_blocked(line):
            return
        self.text.config(state=tk.NORMAL)
        self.text.insert(line + ".0", BULLET_MARK)
        self.mark_modified()

    def insert_check(self):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        self.text.config(state=tk.NORMAL)
        sel = self.text.tag_ranges("sel")
        # 框锚在**行首**，不跟光标走：待办是行级标记（一个框领一整句），
        # 和列表 / 序号按钮同一个规矩。以前插在光标处，光标停在句子中间时
        # 框就落在字里 —— 看着像个正常待办，回车却续接不出来，点它也只是
        # 把后面那截划掉。选中文字时以选区起点所在的行为准。
        ln = str(self.text.index(sel[0] if sel else "insert")).split(".")[0]
        if self._title_line_blocked(ln):
            return
        if split_list_prefix(self.text.get(ln + ".0", ln + ".end"))[0] == "check":
            return                      # 这一行已经是待办了，别叠第二个框
        self._add_checkbutton(checked=False, index=ln + ".0")
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
        self.text.window_create(index, window=blk, padx=0, pady=6)
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

    def insert_numbered(self):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        line = self.text.index("insert").split(".")[0]
        if self._title_line_blocked(line):
            return
        self.text.config(state=tk.NORMAL)
        self.text.insert(line + ".0", NUM_MARK)
        self.mark_modified()

    def insert_triangle(self):
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        line = self.text.index("insert").split(".")[0]
        if self._title_line_blocked(line):
            return
        self.text.config(state=tk.NORMAL)
        self.text.insert(line + ".0", TRI_MARK)
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
            # 补成「缩进 + 序号 + 不换行空格」，与 insert_numbered / 回车续接的形态一致
            self.text.delete(ln + ".0", "%s.%d" % (ln, consume))
            self.text.insert(ln + ".0", LI_INDENT + "%d.%s" % (extra or 1, _MARK_NBSP))
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

    # ---------- 任务提醒（正文里的小闹钟） ----------
    # 一条提醒在正文里就是一枚 16px 的位图（icon/notice.png），数据分两处放：
    #   self._alarms —— "alarm:<id>" → {"id","text","at"}，只在当前笔记打开时有效
    #   content      —— ["alarm", {...}]，落库的唯一形态（见 serialize）
    # 「响过没有」两边都不放，记在 settings["alarm_fired"]（理由见 alarm.py 文件头）。
    #
    # 为什么用 text.image_create 而不是像待办那样用字面字符：待办的方框是字体里的
    # 一个字形，而这里是用户给的一张彩色图 —— 造字这条路走不通（Tk 8.6 还只认 BMP）。
    # 代价是它成了「不占字符的结构项」，序列化必须显式认领（dump 的 image 分支），
    # 忘了认它就会在重开笔记时凭空消失。

    ALARM_TICK_MS = 20000        # 轮询间隔：20 秒。再密没有意义（提醒的容差是分钟级）

    def _alarms_get(self):
        """当前笔记的闹钟表：图片 name → 数据。切笔记时整体换掉。"""
        m = getattr(self, "_alarms", None)
        if not isinstance(m, dict):
            m = self._alarms = {}
        return m

    def _alarm_photo(self):
        """正文里那枚小闹钟。尺寸固定，全程序共用一张。

        ⚠️ 必须挂在实例上保活：PhotoImage 一旦被 GC，Tk 里那个位置就变成空白，
        而且不会当场报错 —— 要等下一次重绘才显形，极难查。
        """
        if not hasattr(self, "_alarm_img"):
            try:
                self._alarm_img = _png_icon_photo(self.root, "notice.png", 16)
            except Exception:
                self._alarm_img = None
        return self._alarm_img

    def _alarm_add(self, idx, data, move=True):
        """在 idx 处放一枚闹钟并登记数据；图缺失时返回 False（正文不动）。

        渲染旧笔记时 move=False —— 那时候光标该由载入流程自己摆，不能被这里挪走。
        """
        img = self._alarm_photo()
        if img is None:
            return False
        d = {"id": data.get("id") or alarm.new_id(),
             "text": alarm.normalize_text(data.get("text")),
             "at": float(data.get("at") or 0)}
        name = "alarm:" + d["id"]
        try:
            self.text.image_create(idx, image=img, name=name)
            if move:
                self.text.mark_set("insert", "%s +1c" % idx)
        except Exception:
            return False
        self._alarms_get()[name] = d
        return True

    def insert_alarm(self):
        """工具栏「任务提醒」：填内容 + 时间，然后在光标处放一枚小闹钟。"""
        if not self.current_note():
            return
        if self._refuse_readonly():
            return
        got = self._alarm_dialog()
        if not got:
            return
        txt, at = got
        if self._alarm_add("insert", {"id": alarm.new_id(), "text": txt, "at": at}):
            self.mark_modified()

    def _alarm_dialog(self, data=None):
        """填「提醒内容 + 日期 + 时间」。返回 `(内容, 时间戳)` 或 None。

        日期用**日历**、时间用**时/分两个 Spinbox**，都不是手打输入框：中文输入法
        下 `2026-10-30 18:00` 里的连字符和冒号十有八九会敲成全角，手打还容易写出
        `25:00`、`2月30日`；控件把范围锁死，点一下就选定。
        「只做标记不提醒」由上面的开关表达（`at` 为 0）。
        编辑和新建共用这一个框，编辑时把原值填进去。
        """
        p = self.pal
        is_new = data is None
        data = data or {}
        at0 = data.get("at") or 0
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
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        def label(t):
            tk.Label(body, text=t, bg=bg, fg=soft,
                     font=(FONT[0], 9)).pack(anchor="w", padx=14, pady=(9, 0))

        def entry(value):
            e = tk.Entry(body, bg=ed, fg=text_fg, insertbackground=accent,
                         relief=tk.FLAT, font=(FONT[0], 10), width=34,
                         highlightthickness=1,
                         highlightbackground=_mix_hex(ed, border, 0.7),
                         highlightcolor=accent)
            e.pack(fill=tk.X, padx=14, pady=(3, 0), ipady=3)
            e.insert(0, value)
            return e

        label("提醒什么（必填）")
        e_text = entry(data.get("text") or "")

        # ---- 日期：日历控件 ----
        # 不再用文本框手打。中文输入法下 `2026-10-30` 里的连字符常被敲成全角，
        # 而「10-30」到底是今年还是明年全靠猜；日历点一下就锁定，还顺带能看见
        # 今天/明天在星期的哪个位置。固定 6 行（见 alarm.month_grid），翻月时
        # 高度不跳，‹ › 不会上下窜。
        label("提醒日期")
        chosen = [day0]                      # 选中的那一天
        shown = [day0.year, day0.month]      # 当前显示的月份（翻月后与 chosen 不同）
        nums = [alarm.month_grid(shown[0], shown[1])]
        cal_on = [True]                      # 「到点提醒」关掉后日历一起灰掉

        cal = tk.Frame(body, bg=bg)
        cal.pack(fill=tk.X, padx=10, pady=(2, 0))

        def mini(parent, text, cmd, size=13, color=None):
            """日历头部的小按钮（‹ › 今天）。用 Label 而不是 Button：
            Button 自带边框、内边距和按下高亮，在这个扁平的对话框里很扎眼。"""
            lb = tk.Label(parent, text=text, bg=bg, fg=color or soft,
                          font=(FONT[0], size), bd=0, padx=7, pady=0,
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
                                                                padx=(0, 6))
        # 先左后右地 pack：‹ 最左、标题居中、› 紧跟。标题给固定 width，翻月时
        # 字数变化（10月→11月）不会把两个箭头推来推去。
        mini(head, "‹", lambda: turn(-1)).pack(side=tk.LEFT, padx=(6, 0))
        ttl = tk.Label(head, text="", bg=bg, fg=text_fg, font=(FONT[0], 10),
                       bd=0, padx=0, pady=0, width=9, anchor="w")
        ttl.pack(side=tk.LEFT)
        mini(head, "›", lambda: turn(1)).pack(side=tk.LEFT)

        net = tk.Frame(cal, bg=bg)
        net.pack(fill=tk.X, pady=(1, 0))
        for c in range(7):
            # uniform 让七列等宽：否则「30」那种两位数会把整列撑宽，格子歪掉
            net.columnconfigure(c, weight=1, uniform="cal")
        for c, wtxt in enumerate(alarm.WEEKDAYS):
            tk.Label(net, text=wtxt, bg=bg, fg=soft, font=(FONT[0], 8),
                     bd=0, padx=0, pady=0).grid(row=0, column=c, pady=(0, 1))

        cells = []
        for r in range(6):
            for c in range(7):
                lb = tk.Label(net, text="", bg=bg, fg=text_fg,
                              font=(FONT[0], 9), bd=0, padx=0, pady=0)
                lb.grid(row=r + 1, column=c, sticky="nsew", ipady=1)
                cells.append(lb)

        def paint_cell(i, hovered=False):
            lb, d = cells[i], nums[0][i]
            if not d:
                lb.configure(text="", bg=bg, cursor="")
                return
            cur = datetime.date(shown[0], shown[1], d)
            if cur == chosen[0]:
                # 选中的一天：实心 accent + 白字。比加个圈更醒目，也不占格子
                lb.configure(text=str(d), bg=accent, fg="#ffffff",
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

        label("提醒时间")
        trow = tk.Frame(body, bg=bg)
        trow.pack(fill=tk.X, padx=14, pady=(2, 0))

        def spin(lo, hi, value):
            """时/分选择框。format 两位补零，防止显示出 `8` 这种不齐的数字。"""
            sp = tk.Spinbox(trow, from_=lo, to=hi, increment=1, wrap=True,
                            format="%02.0f", width=2, font=(FONT[0], 11),
                            bd=0, relief=tk.FLAT, justify="center",
                            bg=ed, fg=text_fg, insertbackground=accent,
                            buttonbackground=_mix_hex(ed, text_fg, 0.10),
                            disabledbackground=bg, disabledforeground=soft,
                            highlightthickness=1,
                            highlightbackground=_mix_hex(ed, border, 0.7),
                            highlightcolor=accent)
            sp.delete(0, tk.END)
            sp.insert(0, "%02d" % value)
            sp.pack(side=tk.LEFT, ipady=2)
            return sp

        sp_h = spin(0, 23, hh0)
        tk.Label(trow, text=":", bg=bg, fg=text_fg,
                 font=(FONT[0], 11)).pack(side=tk.LEFT, padx=2)
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
                            padx=9, pady=1, cursor="hand2")
        btn_now.pack(side=tk.LEFT, padx=(8, 0))

        # 到点提醒：不勾就是「只做标记」（at=0，永远不响）。原来的表达方式是
        # 「时间框留空」，换成 Spinbox 之后没有空值，只能用一个显式开关。
        var_on = tk.BooleanVar(value=True if is_new else bool(at0))
        chk = tk.Checkbutton(trow, text="到点提醒", variable=var_on, bg=bg,
                             fg=text_fg, font=(FONT[0], 9), bd=0,
                             activebackground=bg, activeforeground=text_fg,
                             selectcolor=_mix_hex(bg, text_fg, 0.12),
                             cursor="hand2", highlightthickness=0)
        chk.pack(side=tk.LEFT, padx=(14, 0))

        def toggle(*_a):
            # 不提醒时连日期一起灰掉：留着能编辑却不生效，比直接禁用更让人困惑
            cal_on[0] = bool(var_on.get())
            st = tk.NORMAL if cal_on[0] else tk.DISABLED
            for w in (sp_h, sp_m, btn_now):
                w.configure(state=st)
            paint()

        chk.configure(command=toggle)
        toggle()

        err = tk.Label(body, text="", bg=bg, fg=p.get("danger", "#c0392b"),
                       font=(FONT[0], 9), anchor="w")
        err.pack(fill=tk.X, padx=14, pady=(7, 0))

        row = tk.Frame(body, bg=bg)
        row.pack(fill=tk.X, padx=14, pady=(9, 12))
        out = [None]

        def ok(_e=None):
            txt = alarm.normalize_text(e_text.get())
            if not txt:
                err.configure(text="请填写提醒内容")
                e_text.focus_set()
                return "break"
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
                ts, msg = alarm.parse_when(chosen[0].strftime("%Y-%m-%d"),
                                           "%02d:%02d" % (hh, mm))
            else:
                ts, msg = 0.0, ""
            if msg:
                err.configure(text=msg)
                return "break"
            out[0] = (txt, ts)
            win.destroy()
            return "break"

        def cancel(_e=None):
            win.destroy()
            return "break"

        tk.Button(row, text="确定", command=ok, relief=tk.FLAT, bd=0,
                  bg=accent, fg="#ffffff", activebackground=accent,
                  activeforeground="#ffffff", font=(FONT[0], 10),
                  padx=16, pady=4, cursor="hand2").pack(side=tk.RIGHT)
        tk.Button(row, text="取消", command=cancel, relief=tk.FLAT, bd=0,
                  bg=_mix_hex(bg, text_fg, 0.10), fg=text_fg,
                  activebackground=_mix_hex(bg, text_fg, 0.18),
                  activeforeground=text_fg, font=(FONT[0], 10),
                  padx=16, pady=4, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 8))

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
        data = self._alarms_get().get(name)
        if not data:
            return
        got = self._alarm_dialog(data)
        if not got:
            return
        data["text"], data["at"] = got
        self.mark_modified()

    def _alarm_pos(self, name):
        """这枚闹钟在正文里的索引；找不到返回 None。"""
        try:
            for key, value, index in self.text.dump("1.0", tk.END, image=True):
                if key == "image" and value == name:
                    return index
        except Exception:
            pass
        return None

    def _alarm_remove(self, name):
        """删掉闹钟图 = 取消这条提醒（数据随图一起进垃圾桶）。"""
        pos = self._alarm_pos(name)
        if not pos:
            return
        self._alarm_tip_hide()
        self.text.edit_separator()
        try:
            self.text.delete(pos)
        except Exception:
            return
        finally:
            self.text.edit_separator()
        self._alarms_get().pop(name, None)
        self.mark_modified()

    # ----- 悬浮提示与右键 -----

    def _alarm_at(self, x, y):
        """(x, y) 落在一枚闹钟上时返回它的 name，否则 None。"""
        try:
            pos = self.text.index("@%d,%d" % (int(x), int(y)))
            for key, value, _i in self.text.dump(pos, "%s +1c" % pos, image=True):
                if key == "image" and value in self._alarms_get():
                    return value
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
        self._alarm_tip_show(event.x_root + 14, event.y_root + 18, title, sub)

    def _alarm_tip_show(self, x, y, title, sub):
        p = self.pal
        self._alarm_tip_hide()
        try:
            win = tk.Toplevel(self.root)
            win.wm_overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=_popover_border(p))
            body = tk.Frame(win, bg=_popover_bg(p))
            body.pack(padx=1, pady=1)
            # ⚠️ Label 的 padx/pady 只吃单值（"6 0" 会报 bad screen distance），
            # 上下不对称要靠 pack 的 pady 来表达
            for txt, fg, size, gap in ((title, p.get("text", "#222222"), 10, (6, 0)),
                                       (sub, p.get("soft", "#76767e"), 9, (1, 6))):
                tk.Label(body, text=txt, bg=_popover_bg(p), fg=fg,
                         font=(FONT[0], size), bd=0, justify="left", anchor="w",
                         wraplength=300, padx=10).pack(fill=tk.X, pady=gap)
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry("+%d+%d" % (max(0, min(int(x), sw - w - 4)),
                                     max(0, min(int(y), sh - h - 4))))
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
        m.add("编辑提醒", lambda: self._alarm_edit(name))
        m.add("删除闹钟", lambda: self._alarm_remove(name), danger=True)
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
        return bool(self._alarm_fired_map().get(self._alarm_key(data)))

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
        self._alarm_job = None
        self._alarm_start_tick()

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
                if not d.get("at") or fired.get("%s|%s" % (nid, d.get("id") or "")):
                    continue
                items.append((nid, d))
        fire, stale = alarm.due(items, now)
        changed = False
        for nid, d in stale:
            fired["%s|%s" % (nid, d.get("id") or "")] = now
            changed = True
        if fire:
            nid, d = fire[0]            # 一轮只弹一条，剩下的下一轮排队
            fired["%s|%s" % (nid, d.get("id") or "")] = now
            changed = True
            self._alarm_popup(nid, d)
            self._alarm_hover = None    # 状态变了，下次悬停要重算文案
        if changed:
            try:
                save_data(self.data)
            except Exception:
                pass

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
        body.pack(padx=1, pady=1)
        # Label 的 padx/pady 只吃单值，上下留白一律走 pack 的 pady
        tk.Label(body, text="任务提醒 · " + (alarm.fmt_at(data.get("at")) or "未设时间"),
                 bg=bg, fg=accent, font=(FONT[0], 9), bd=0,
                 padx=14, anchor="w").pack(fill=tk.X, pady=(10, 0))
        tk.Label(body, text=data.get("text") or "", bg=bg,
                 fg=p.get("text", "#222222"), font=(FONT[0], 11), bd=0,
                 padx=14, anchor="w", justify="left",
                 wraplength=260).pack(fill=tk.X, pady=(2, 0))
        row = tk.Frame(body, bg=bg)
        row.pack(fill=tk.X, padx=14, pady=(9, 11))
        tk.Button(row, text="知道了", command=win.destroy, relief=tk.FLAT, bd=0,
                  bg=accent, fg="#ffffff", activebackground=accent,
                  activeforeground="#ffffff", font=(FONT[0], 9),
                  padx=14, pady=3, cursor="hand2").pack(side=tk.RIGHT)
        try:
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry("+%d+%d" % (sw - w - 28, sh - h - 96))
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
            pw = self._ask_password("已加密笔记", "这篇笔记已加密，请输入口令：")
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

    def set_note_password(self, nid=None):
        """给笔记设置口令（已有口令则改为「修改口令」）。"""
        n = self._pick_note(nid)
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        if self.is_sample_note(n):
            self._show_info("无法加密", "内置示例笔记是只读的，不需要（也不能）加密。")
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
        n["sec"] = blob
        self._keys[n["id"]] = key
        save_data(self.data)
        self.refresh_notes()
        # 「加密笔记」是智能分类，条数按密文列算 —— 就地刷新左栏计数，
        # 否则要点一圈文件夹（重建行）才会发现这篇已经算进去了。
        self._refresh_folder_counts()
        self._show_info("已加密",
                        "这篇笔记已加密。\n\n"
                        "切换笔记或重新打开程序后，需要输入口令才能查看内容。")

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
        secret.stamp_hint(blob, self._head_char(n))
        n["sec"] = blob
        self._keys[n["id"]] = key
        save_data(self.data)
        self._show_info("已更新", "加密口令已更新。")

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
        folder = "f_work" if f in SMART_FOLDERS else f
        import time
        now = time.strftime("%Y-%m-%d", time.localtime())
        n = {"id": uid(), "folder": folder,
             "content": _t(now, ["title"]) + [["t", "\n", []]],
             "pinned": False, "created": time.time() * 1000,
             "updated": time.time() * 1000}
        self.data["notes"].insert(0, n)
        switched = f in SMART_FOLDERS
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
            # 智能文件夹里新建会切换视图（列表内容整体变了）→ 只能整列重建
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

    def _refuse_sample_delete(self):
        """删除内置示例笔记时的统一提示。"""
        self._show_info("无法删除",
                        "「%s」是内置的使用说明，既不能删除，也不能编辑。\n\n"
                        "它里面列出了全部快捷键，随时可以回来看。\n"
                        "想写笔记请点右上角「新建笔记」。" % ("欢迎使用" + APP_NAME))

    def delete_note(self):
        n = self.current_note()
        if not n:
            self._show_info("提示", "请先在中间列表选择一条笔记。")
            return
        if self.is_sample_note(n):
            self._refuse_sample_delete()
            return
        if not self._ask_confirm("删除", "确定删除这条笔记？", ok_text="删除", danger=True):
            return
        nid = n["id"]
        self.data["notes"] = [x for x in self.data["notes"] if x["id"] != nid]
        # 标签一并收掉，并把编辑器顺到邻居上（右邻 → 左邻）；没有其它标签才空着。
        # （_render_tabs 也会剪掉库里已经没有的 id，这里先剪是为了算出「下一个是谁」）
        self.data["current_note"] = self._tab_drop(nid)
        self._note_sel.discard(nid)
        save_data(self.data)
        # 只移除这一行 + 就地刷新计数：文件夹行与其余笔记都不重建 → 不闪烁
        if nid in self._rows:
            self._remove_note_rows([nid])
        else:
            self.refresh_notes()
        self._active_title = self._active_preview = self._active_date = None
        self._refresh_folder_counts()
        self._restyle_notes()
        self.load_editor()

    def toggle_pin(self):
        n = self.current_note()
        if not n:
            return
        n["pinned"] = not n["pinned"]
        n["updated"] = __import__("time").time() * 1000
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
            self.data["folders"].append(f)
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

    def del_folder(self, fid=None):
        target = fid or self.data["current_folder"]
        f = next((x for x in self.data["folders"] if x["id"] == target), None)
        if not f or f.get("smart"):
            self._show_info("提示", "「全部笔记 / 星标笔记 / 加密笔记」是智能分类，不能删除。")
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
        if not self._ask_confirm("删除文件夹", msg, ok_text="删除", danger=True):
            return
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
            m.add("删除选中的 %d 个文件夹" % len(self._folder_sel), icon="trash",
                  danger=True,
                  command=lambda: self._delete_folders(list(self._folder_sel)))
        else:
            m.add("重命名", icon="pencil", command=lambda: self.rename_folder(fid))
            if not f.get("smart"):
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
            else:
                m.add("新建子文件夹（智能分类下不能建）", icon="folder", enabled=False)
                m.add("删除文件夹（智能文件夹不可删）", icon="trash", enabled=False)
        m.popup(event.x_root, event.y_root)

    def _move_notes_out(self, fid):
        """右键 → 移动笔记：把该文件夹下的笔记全部移到另一个文件夹。"""
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        if f.get("smart"):
            self._show_info("提示", "智能分类是筛选出来的视图，不能整体移动。")
            return
        notes = self.folder_notes(fid)
        if not notes:
            self._show_info("提示", "文件夹「%s」下没有笔记。" % f["name"])
            return
        # 自己的子文件夹不算目标：挪进子层之后父视图照样能看见它们（父级含子树计数），
        # 看起来像「什么都没动」，但笔记的归属确实变了 —— 这种操作不给人做。
        kin = set(subtree_ids(fid, self.data["folders"]))
        targets = [x for x in self.data["folders"]
                   if not x.get("smart") and x["id"] not in kin]
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
