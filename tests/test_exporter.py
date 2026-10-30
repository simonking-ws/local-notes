# -*- coding: utf-8 -*-
"""导出层单测：纯文本 / Markdown 的还原。

内容一律手搓 run 列表（存储格式就是逐字符的 ["t", 字, [标签]]），
不走 GUI、不碰数据库。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from md_conv import LI_INDENT, _MARK_NBSP, TODO_OFF, TODO_ON, BULLET_GLYPH, TRI_MARK
import exporter


def runs(text, tags=()):
    """把一段文字拆成存储层那种逐字符 run。"""
    return [["t", ch, list(tags)] for ch in text]


def line(text, tags=()):
    return runs(text, tags) + [["t", "\n", []]]


def note(*chunks):
    out = []
    for c in chunks:
        out.extend(c)
    return out


class TestBlocks(unittest.TestCase):
    def test_plain_lines(self):
        c = note(line("标题", ["title"]), line("第一段"), line("第二段"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual([b["kind"] for b in bs], ["line", "line", "line"])
        self.assertEqual(exporter.line_text(bs[0]), "标题")
        self.assertEqual(exporter.line_tags(bs[0]), {"title"})

    def test_blank_line_kept(self):
        c = note(line("a"), line(""), line("b"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual(len(bs), 3)
        self.assertEqual(exporter.line_text(bs[1]), "")

    def test_code_block_swallows_own_newline(self):
        c = note(line("上文"), [["code", "python", "print(1)\nprint(2)"]],
                 [["t", "\n", []]], line("下文"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual([b["kind"] for b in bs], ["line", "code", "line"])
        self.assertEqual(bs[1]["lang"], "python")
        self.assertEqual(bs[1]["code"], "print(1)\nprint(2)")

    def test_hr(self):
        c = note(line("上"), [["hr"], ["t", "\n", []]], line("下"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual([b["kind"] for b in bs], ["line", "hr", "line"])

    def test_trailing_newline_dropped(self):
        c = note(line("只有一行"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual(len(bs), 1)

    def test_legacy_window_item_ignored(self):
        c = note(line("a"), [["w", True, []]], line("b"))
        bs = exporter.blocks_from_content(c)
        self.assertEqual(len(bs), 2)

    def test_empty(self):
        self.assertEqual(exporter.blocks_from_content([]), [])
        self.assertEqual(exporter.blocks_from_content(None), [])


class TestPlain(unittest.TestCase):
    def test_headings_drop_marks(self):
        c = note(line("标题", ["title"]), line("小节", ["h2"]), line("正文"))
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(c)),
                         "标题\n小节\n正文\n")

    def test_bullet_marker(self):
        c = note(line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "一条", ["body"]))
        out = exporter.plain_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- 一条\n")

    def test_triangle_marker(self):
        c = note(line(TRI_MARK + "三角", ["body"]))
        out = exporter.plain_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- 三角\n")

    def test_numbered_keeps_digits(self):
        c = note(line(LI_INDENT + "1." + _MARK_NBSP + "第一步", ["body"]))
        out = exporter.plain_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "1. 第一步\n")

    def test_check_state(self):
        off = note(line(LI_INDENT + TODO_OFF + _MARK_NBSP + "没做", ["body"]))
        on = note(line(LI_INDENT + TODO_ON + _MARK_NBSP + "做了", ["body", "done"]))
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(off)),
                         "[ ] 没做\n")
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(on)),
                         "[x] 做了\n")

    def test_quote_indented(self):
        c = note(line("引言", ["body", "quote"]))
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(c)),
                         "  引言\n")

    def test_code_verbatim(self):
        c = note([["code", "", "a\n  b"]], [["t", "\n", []]])
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(c)),
                         "a\n  b\n")

    def test_hr(self):
        c = note([["hr"], ["t", "\n", []]])
        self.assertEqual(exporter.plain_text(exporter.blocks_from_content(c)),
                         "-" * 24 + "\n")


class TestMarkdown(unittest.TestCase):
    def test_title_and_h2(self):
        c = note(line("标题", ["title"]), line("小节", ["h2"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "# 标题\n## 小节\n")

    def test_hard_break_on_paragraph(self):
        c = note(line("第一行", ["body"]), line("第二行", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "第一行  \n第二行  \n")

    def test_emphasis(self):
        c = note(runs("粗", ["bold"]) + runs("斜", ["italic"])
                 + runs("粗斜", ["bold", "italic"]) + runs("删", ["strike"])
                 + runs("下", ["underline"]) + [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        # 相邻两段强调在源码里会拼出 `***` / `****` 的接缝（粗+斜连着写就是这样），
        # CommonMark 按「3 的倍数」规则能正确切开，所以这里只管原样输出。
        self.assertEqual(out, "**粗***斜****粗斜***~~删~~<u>下</u>  \n")

    def test_inline_code_not_escaped(self):
        c = note(runs("a*b", ["icode"]) + [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "`a*b`  \n")

    def test_escape_emphasis_chars(self):
        c = note(line("2 * 3 = 6", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "2 \\* 3 = 6  \n")

    def test_link(self):
        c = note(runs("点这里", ["lk_1"]) + [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c),
                                     links={"lk_1": "https://example.com"})
        self.assertEqual(out, "[点这里](https://example.com)  \n")

    def test_link_missing_url_keeps_text(self):
        c = note(runs("点这里", ["lk_1"]) + [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "点这里  \n")

    def test_fence(self):
        c = note([["code", "python", "x=1"]], [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "```python\nx=1\n```\n")

    def test_fence_without_lang(self):
        c = note([["code", "", "x=1"]], [["t", "\n", []]])
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "```\nx=1\n```\n")

    def test_hr(self):
        c = note([["hr"], ["t", "\n", []]])
        self.assertEqual(exporter.markdown_text(exporter.blocks_from_content(c)),
                         "---\n")

    def test_bullet_and_numbered(self):
        c = note(line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "一", ["body"]),
                 line(LI_INDENT + "2." + _MARK_NBSP + "二", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- 一\n2. 二\n")

    def test_nested_level(self):
        c = note(line(LI_INDENT * 2 + BULLET_GLYPH + _MARK_NBSP + "深", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "  - 深\n")

    def test_check(self):
        c = note(line(LI_INDENT + TODO_ON + _MARK_NBSP + "完成", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- [x] 完成\n")

    def test_quote(self):
        c = note(line("引用一句", ["body", "quote"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "> 引用一句  \n")

    def test_list_isolated_by_blank_line(self):
        # 列表后面紧跟普通段落：不空一行的话 Markdown 会把它并进列表项
        c = note(line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "条目", ["body"]),
                 line("说明文字", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- 条目\n\n说明文字  \n")

    def test_list_after_paragraph_gets_blank_line(self):
        c = note(line("前言", ["body"]),
                 line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "条目", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "前言  \n\n- 条目\n")

    def test_consecutive_list_items_no_blank(self):
        c = note(line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "一", ["body"]),
                 line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "二", ["body"]))
        out = exporter.markdown_text(exporter.blocks_from_content(c))
        self.assertEqual(out, "- 一\n- 二\n")


class TestMarkerInfo(unittest.TestCase):
    def test_bullet(self):
        c = note(line(LI_INDENT + BULLET_GLYPH + _MARK_NBSP + "x", ["body"]))
        b = exporter.blocks_from_content(c)[0]
        mi = exporter.marker_info(b)
        self.assertEqual(mi["kind"], "bullet")
        self.assertEqual(mi["level"], 0)
        self.assertEqual(exporter.line_text(b)[mi["cut"]:], "x")

    def test_numbered_text(self):
        c = note(line(LI_INDENT + "12." + _MARK_NBSP + "x", ["body"]))
        mi = exporter.marker_info(exporter.blocks_from_content(c)[0])
        self.assertEqual((mi["kind"], mi["text"]), ("numbered", "12."))

    def test_check_checked(self):
        c = note(line(LI_INDENT + TODO_ON + _MARK_NBSP + "x", ["body"]))
        mi = exporter.marker_info(exporter.blocks_from_content(c)[0])
        self.assertEqual((mi["kind"], mi["checked"]), ("check", True))

    def test_plain_line(self):
        c = note(line("普通一行", ["body"]))
        mi = exporter.marker_info(exporter.blocks_from_content(c)[0])
        self.assertEqual((mi["kind"], mi["cut"], mi["level"]), (None, 0, 0))


class TestFilename(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(exporter.safe_filename("周会待办", "md"), "周会待办.md")

    def test_illegal_chars(self):
        self.assertEqual(exporter.safe_filename('a/b:c*d?"e"<f>|g', "txt"),
                         "a b c d e f g.txt")

    def test_empty(self):
        self.assertEqual(exporter.safe_filename("   ", "txt"), "未命名笔记.txt")

    def test_trailing_dots(self):
        self.assertEqual(exporter.safe_filename("标题...", "txt"), "标题.txt")

    def test_long_name(self):
        name = exporter.safe_filename("长" * 200, "txt")
        self.assertLessEqual(len(name), 80 + 4)


if __name__ == "__main__":
    unittest.main()
