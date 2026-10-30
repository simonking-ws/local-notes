# -*- coding: utf-8 -*-
"""md_conv 的测试（纯函数，不建 GUI，标准库 unittest 即可跑）。

这个文件存在的意义：编辑器里「敲 ## 空格 就变标题」这类行为，靠人工点界面
验证既慢又容易漏。把「什么算前缀」抽成纯函数后，边界（### 不该变 H2、
1. 两空格不该重复转换、网址末尾的句号不该算进链接）都能写成断言。

运行：
    python tests\\test_md_conv.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import md_conv                                                  # noqa: E402
from md_conv import (detect_prefix, is_hr, find_urls,           # noqa: E402
                     parse_fence, split_list_prefix, find_time_call, MD_LINK_RE,
                     LI_INDENT, BULLET_MARK, NUM_MARK, TRI_MARK,
                     BULLET_GLYPH, TODO_OFF, TODO_ON, TODO_GAP)


class DetectPrefixTest(unittest.TestCase):
    """块级前缀识别：kind / 要删掉几个字符 / 附加信息。"""

    def test_h1_and_h2(self):
        self.assertEqual(detect_prefix("# 标题"), ("title", 2, None))
        self.assertEqual(detect_prefix("## 小标题"), ("h2", 3, None))

    def test_h3_not_supported(self):
        # 项目没有 H3 的字号层级，### 必须原样保留，不能被当成 H2
        self.assertEqual(detect_prefix("### 三级")[0], None)

    def test_hash_without_space_is_text(self):
        # "#标签" 不是标题
        self.assertEqual(detect_prefix("#标签")[0], None)
        self.assertEqual(detect_prefix("##")[0], None)

    def test_quote(self):
        self.assertEqual(detect_prefix("> 引用"), ("quote", 2, None))
        self.assertEqual(detect_prefix(">引用")[0], None)

    def test_bullet_variants(self):
        for s in ("- 项", "* 项", "+ 项"):
            self.assertEqual(detect_prefix(s), ("bullet", 2, None))

    def test_numbered(self):
        self.assertEqual(detect_prefix("1. 项"), ("numbered", 3, 1))
        self.assertEqual(detect_prefix("42. 项"), ("numbered", 4, 42))

    def test_numbered_already_normalized(self):
        # "1.  " 已经是编辑器里的形态（两空格），不能再转一次，否则死循环
        self.assertEqual(detect_prefix("1.  项")[0], None)

    def test_fence(self):
        self.assertEqual(detect_prefix("```"), ("fence", 3, ""))
        self.assertEqual(detect_prefix("```python"), ("fence", 9, "python"))
        self.assertEqual(detect_prefix("``` python "), ("fence", 11, "python"))

    def test_hr(self):
        self.assertEqual(detect_prefix("---")[0], "hr")
        self.assertEqual(detect_prefix("***")[0], "hr")
        self.assertEqual(detect_prefix("___")[0], "hr")
        self.assertTrue(is_hr("- - -"))
        self.assertFalse(is_hr("--"))
        self.assertFalse(is_hr("---- 后面还有字"))

    def test_minus_is_not_hr(self):
        # "- 项" 是列表，不是分割线
        self.assertEqual(detect_prefix("- 项")[0], "bullet")

    def test_check_markdown(self):
        """`-[] ` + 空格 → 待办（consume 含尾部那个空格）。

        只认紧凑写法：`- [ ]` 敲到 `- ` 时已经被项目符号吃掉了。
        与 `- ` 同一套规则 —— 前缀后面必须紧跟空格，`-[]项目` 只是正文。
        """
        self.assertEqual(detect_prefix("-[] "), ("check", 4, None))
        self.assertEqual(detect_prefix("-[]买牛奶 ")[0], None)
        self.assertEqual(detect_prefix("-[]"), (None, 0, None))

    def test_triangle_markdown(self):
        """`-> ` + 空格 → 三角列表。"""
        self.assertEqual(detect_prefix("-> "), ("triangle", 3, None))
        self.assertEqual(detect_prefix("-> 项")[0], "triangle")
        self.assertEqual(detect_prefix("->项 ")[0], None)
        # 别被分割线规则抢走
        self.assertNotEqual(detect_prefix("-> ")[0], "hr")

    def test_rendered_big_bullet_ignored(self):
        # 现行项目符号是 ●（U+25CF），同样不能被再次识别
        self.assertEqual(detect_prefix(BULLET_MARK + "项目")[0], None)

    def test_rendered_bullet_ignored(self):
        # 编辑器里存的是渲染后的 "•  "，不能再次识别
        self.assertEqual(detect_prefix("\u2022  \u9879")[0], None)

    def test_plain_text(self):
        for s in ("", "普通正文", "  # 缩进的井号", "1.没有空格"):
            self.assertEqual(detect_prefix(s)[0], None, s)


class ListPrefixTest(unittest.TestCase):
    """行首列表标记的拆分：要分清「缩进 + 标记」和「标记后面跟的文字」。"""

    def test_new_form_has_indent(self):
        kind, prefix = split_list_prefix(BULLET_MARK + "项目")
        self.assertEqual(kind, "bullet")
        self.assertEqual(prefix, BULLET_MARK)
        self.assertTrue(prefix.startswith(LI_INDENT))

    def test_legacy_without_indent_still_recognized(self):
        # 存量笔记里是旧版直接插的 "•  "（半角空格），没有缩进，必须仍能识别，
        # 否则老笔记里按回车就不再续接列表了。返回的是**现行形态**（大圆点 + NBSP），
        # 于是续接出来的新行自带新标记 —— 这是老笔记唯一的迁移途径
        self.assertEqual(split_list_prefix("\u2022  项目"),
                         ("bullet", BULLET_GLYPH + "\u00a0\u00a0"))
        self.assertEqual(split_list_prefix("\u27a4  项目"),
                         ("triangle", "\u27a4\u00a0\u00a0"))
        self.assertEqual(split_list_prefix("\u25b8  项目"),
                         ("triangle", "\u25b8\u00a0\u00a0"))

    def test_bullet_glyph_is_big_dot(self):
        """项目符号用 U+25CF（●），不是 U+2022（•）。

        • 在 12pt 正文里墨迹只有 5x4，比汉字小一大截，看着像句号；● 是 8x7。
        这是「点有点小」那次反馈的结论，写死断言免得哪天被顺手改回去。
        """
        self.assertEqual(BULLET_GLYPH, "\u25cf")

    def test_mark_tails_are_the_same_gap(self):
        """四种列表的「标记 → 正文」间隙必须是同一串字符（TODO_GAP）。

        用户反馈过「工具栏按钮和 markdown 产生的距离不一致」：待办原先用图标字体
        的一个 NBSP（17px），其余三种是两个正文宽 NBSP（12px）。这条断言把四种
        锁死在同一常量上，谁单独改都会红。
        """
        self.assertEqual(TODO_GAP, "\u00a0\u00a0")
        for mark in (BULLET_MARK, NUM_MARK, TRI_MARK):
            self.assertTrue(mark.endswith(TODO_GAP), repr(mark))
        self.assertEqual(split_list_prefix(TODO_OFF + TODO_GAP + "文字"),
                         ("check", TODO_OFF + TODO_GAP))
        self.assertEqual(BULLET_MARK[len(LI_INDENT)], "\u25cf")
        # 老字形必须能被升级成现行字形（否则存量笔记永远是小的那个点）
        self.assertEqual(md_conv._MARK_ALIAS.get("\u2022"), "\u25cf")
        self.assertNotIn(BULLET_GLYPH, md_conv._MARK_ALIAS)   # 现行字形禁止自环

    def test_prefix_length_matches_text(self):
        # prefix 要能直接拿来定位「正文首字」，所以长度必须等于正文里被它占掉
        # 的那一段 —— 老笔记（半角空格）和现形态（NBSP）长度一致，视觉宽度也一致。
        # 首字符可能被归一成现行字形（• → ●），只断言长度与列表种类。
        for mark in ("\u2022  ", "\u2022\u00a0\u00a0", "1.  ", "1.\u00a0\u00a0"):
            _kind, prefix = split_list_prefix(mark + "项目")
            self.assertEqual(len(prefix), len(mark), repr(mark))

    def test_mark_tail_is_never_breakable_space(self):
        # 防回归：标记尾部一旦出现半角空格，Tk 会在那里换行，长列表的第一行
        # 就只剩一个符号（Tk 把后面那串汉字当成不可断的词）
        self.assertNotIn(" ", BULLET_MARK)
        self.assertNotIn(" ", NUM_MARK)
        self.assertNotIn(" ", TRI_MARK)

    def test_numbered_keeps_indent(self):
        self.assertEqual(split_list_prefix(NUM_MARK + "第一条"),
                         ("numbered", NUM_MARK))
        kind, prefix = split_list_prefix(LI_INDENT + "12.  条目")
        self.assertEqual(kind, "numbered")
        self.assertEqual(prefix, LI_INDENT + "12.\u00a0\u00a0")

    def test_todo_prefix(self):
        # 待办勾选框是字面字符：框 + 间隙即为前缀，后面有没有正文都算
        self.assertEqual(split_list_prefix(TODO_OFF + "\u00a0买牛奶"),
                         ("check", TODO_OFF + "\u00a0"))
        self.assertEqual(split_list_prefix(TODO_ON + "\u00a0已完成"),
                         ("check", TODO_ON + "\u00a0"))
        # 空待办行（只剩一个框）也要认出来，否则回车结束列表的分支走不到
        self.assertEqual(split_list_prefix(TODO_OFF + "\u00a0"),
                         ("check", TODO_OFF + "\u00a0"))

    def test_todo_legacy_glyph_normalized(self):
        """老数据的旧字形照样认，但前缀一律返回现行字形（一换一，等长）。

        存量笔记里有三代旧字形：第一代直角方框 ☐ / ☑、短暂用过的空心圆 ⚪、
        上一版的细方框 ▢。认不出来的话那些行会掉成普通正文；返回旧字形的话，
        回车续接 / 悬挂缩进又得各写一份兼容。这里统一在出口归一。

        ⚠️ 现行的 ⬜ / ✅ 自己**不能**进 TODO_LEGACY（它们就是目标字形），否则
        归一表变成自环，以后再加一轮换字形就会互相覆盖。
        """
        self.assertEqual(md_conv.TODO_OFF, "\u2b1c")
        self.assertEqual(md_conv.TODO_ON, "\u2705")
        self.assertNotIn(md_conv.TODO_OFF, md_conv.TODO_LEGACY)
        self.assertNotIn(md_conv.TODO_ON, md_conv.TODO_LEGACY)
        for legacy, want in (("\u2610", md_conv.TODO_OFF), ("\u26aa", md_conv.TODO_OFF),
                             ("\u25a2", md_conv.TODO_OFF), ("\u2611", md_conv.TODO_ON)):
            self.assertEqual(split_list_prefix(legacy + "\u00a0买牛奶"),
                             ("check", want + "\u00a0"), repr(legacy))
            self.assertEqual(
                split_list_prefix("\u3000\u3000" + legacy + "\u00a0缩进待办"),
                ("check", "\u3000\u3000" + want + "\u00a0"), repr(legacy))
            self.assertIsNone(detect_prefix("\u3000\u3000" + legacy + "\u00a0待办")[0])
        # 现行字形也要被认成 check（它现在是全表里唯一没进 legacy 的那个）
        self.assertEqual(split_list_prefix(md_conv.TODO_OFF + "\u00a0买东西"),
                         ("check", md_conv.TODO_OFF + "\u00a0"))

    def test_todo_not_converted_by_markdown(self):
        # "☐ 内容" 不能被当成 markdown 前缀再转一次
        self.assertIsNone(detect_prefix(TODO_OFF + "\u00a0待办")[0])

    def test_not_a_list(self):
        for s in ("", "普通正文", "  •没有空格", "1.没有空格", "\u3000\u3000正文"):
            self.assertEqual(split_list_prefix(s), (None, ""), s)

    def test_rendered_marks_not_reconverted(self):
        # 「敲 - 转成 •（带缩进）之后再敲空格」不能重复转换。新旧两种尾空格都要挡住
        for s in (BULLET_MARK, TRI_MARK, LI_INDENT + "\u25b8  ",
                  LI_INDENT + "\u25b8\u00a0\u00a0", NUM_MARK):
            self.assertEqual(detect_prefix(s + "项目")[0], None, repr(s))


class ParseFenceTest(unittest.TestCase):
    """代码围栏整行识别（回车触发用）。"""

    def test_with_lang(self):
        self.assertEqual(parse_fence("```python"), "python")
        self.assertEqual(parse_fence("``` python "), "python")

    def test_without_lang(self):
        # 空字符串是合法结果（围栏没写语言），调用方必须用 is not None 判断
        self.assertEqual(parse_fence("```"), "")
        self.assertIsNotNone(parse_fence("```"))

    def test_not_a_fence(self):
        for s in ("", "``", "```python extra", "正文 ```python", "```py thon"):
            self.assertIsNone(parse_fence(s), repr(s))


class FindUrlsTest(unittest.TestCase):
    """裸网址识别：只认 http(s)，中文标点和句末标点不能算进去。"""

    def test_simple(self):
        got = find_urls("见 https://example.com/a 这段")
        self.assertEqual(len(got), 1)
        s, e, url = got[0]
        self.assertEqual(url, "https://example.com/a")
        self.assertEqual("见 https://example.com/a 这段"[s:e], url)

    def test_trailing_punctuation_stripped(self):
        got = find_urls("访问 https://a.com/x.")
        self.assertEqual(got[0][2], "https://a.com/x")

    def test_chinese_punctuation_stops_url(self):
        got = find_urls("（https://a.com/x），后面")
        self.assertEqual(got[0][2], "https://a.com/x")

    def test_two_urls(self):
        got = find_urls("http://a.cn 和 https://b.com/p?q=1")
        self.assertEqual([u[2] for u in got],
                         ["http://a.cn", "https://b.com/p?q=1"])

    def test_bare_domain_not_matched(self):
        self.assertEqual(find_urls("example.com 不算"), [])

    def test_offset(self):
        # 调用方传行首的绝对偏移，返回值要能直接拿去 tag_add
        got = find_urls("https://a.com", offset=10)
        self.assertEqual(got[0][0], 10)
        self.assertEqual(got[0][1], 23)

    def test_too_short(self):
        self.assertEqual(find_urls("https://"), [])


class TimeCallTest(unittest.TestCase):
    """date() / datetime()：只在「紧贴光标」时才展开。"""

    def test_date(self):
        self.assertEqual(find_time_call("today date()"), ("date", 6))

    def test_datetime(self):
        # datetime() 比 date() 长，正则必须优先匹配长的那一个，否则会切出一半
        self.assertEqual(find_time_call("t datetime()"), ("datetime", 2))

    def test_whole_line(self):
        self.assertEqual(find_time_call("date()"), ("date", 0))

    def test_not_at_end(self):
        # 后面还有字 → 不是"刚敲完"，不动它
        self.assertIsNone(find_time_call("date() 是日期"))
        self.assertIsNone(find_time_call("date()x"))

    def test_word_prefix_rejected(self):
        # update() 不能被当成 date()
        self.assertIsNone(find_time_call("update()"))
        self.assertIsNone(find_time_call("mydate()"))

    def test_other_text(self):
        self.assertIsNone(find_time_call("普通文字"))
        self.assertIsNone(find_time_call(""))


class MdLinkTest(unittest.TestCase):
    """富链接的取值：显示文字与被收起来的网址。"""

    def test_basic(self):
        m = MD_LINK_RE.match("[官网](https://example.com/a)")
        self.assertEqual(m.group(1), "官网")
        self.assertEqual(m.group(2), "https://example.com/a")

    def test_chinese_text(self):
        m = MD_LINK_RE.search("看这里[点我打开](https://a.cn)")
        self.assertEqual((m.group(1), m.group(2)), ("点我打开", "https://a.cn"))

    def test_two_links(self):
        got = [(m.group(1), m.group(2))
               for m in MD_LINK_RE.finditer("[a](http://a.cn)[b](http://b.cn)")]
        self.assertEqual(got, [("a", "http://a.cn"), ("b", "http://b.cn")])

    def test_url_with_spaces_rejected(self):
        # 网址里不可能有空格：这类写法多半是正文，不该被吞掉
        self.assertIsNone(MD_LINK_RE.search("[x](http://a.cn/a b)"))

    def test_half_link_not_matched(self):
        self.assertIsNone(MD_LINK_RE.search("[只有文字]"))
        self.assertIsNone(MD_LINK_RE.search("[文字](没写完"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
