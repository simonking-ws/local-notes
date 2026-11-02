# -*- coding: utf-8 -*-
"""`@` 关联笔记的纯逻辑单测：触发词识别、候选排序、内链地址。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from mention import (find_mention, note_url, note_id_of_url, mention_text, rank)


def cand(nid, title, sub="", updated=0.0):
    return {"id": nid, "title": title, "sub": sub, "updated": updated}


class TestFindMention(unittest.TestCase):
    def test_at_space_triggers_empty_query(self):
        self.assertEqual(find_mention("@ ", 2), (0, ""))

    def test_query_collected(self):
        self.assertEqual(find_mention("@ 会议", 4), (0, "会议"))

    def test_at_right_after_text_triggers(self):
        """回归：紧跟文字的 `@ + 空格` 也要弹（2026-11-05 用户报障）。

        旧版用「@ 左邻必须是行首 / 空白 / 标点」挡邮箱，顺手把汉字、字母后面
        的 @ 全挡了：「今天开会@ 」「abc@ 」都弹不出候选框。
        """
        self.assertEqual(find_mention("见 @ 会议", 6), (2, "会议"))
        self.assertEqual(find_mention("会议@ ", 4), (2, ""))
        self.assertEqual(find_mention("今天开会@ ", 6), (4, ""))
        self.assertEqual(find_mention("abc@ ", 5), (3, ""))
        self.assertEqual(find_mention("abc@ 会议", 7), (3, "会议"))
        self.assertEqual(find_mention("1.@ 甲", 5), (2, "甲"))

    def test_at_mid_word_is_not_trigger(self):
        # 邮箱 / 用户名里的 @ 后面紧跟的是域名而不是空格，靠「@ + 空格」挡住，
        # 不需要（也不能）再限制 @ 左边是什么字符。
        self.assertIsNone(find_mention("zhang@163.com", 13))
        self.assertIsNone(find_mention("a@b c", 6))
        self.assertIsNone(find_mention("zhang@163.com ", 14))
        self.assertIsNone(find_mention("user@host 继续", 14))

    def test_at_alone_not_trigger(self):
        # 只有 `@`、还没敲空格时不弹（用户可能只是想打一个 @）
        self.assertIsNone(find_mention("@", 1))
        self.assertIsNone(find_mention("你好@", 3))

    def test_punct_before_ok(self):
        self.assertEqual(find_mention("（@ 甲", 4), (1, "甲"))
        self.assertEqual(find_mention("见，@ 甲", 5), (2, "甲"))

    def test_second_space_ends_it(self):
        # 又一个空格 = 已经在写下一句话了
        self.assertIsNone(find_mention("@ 甲 乙", 5))

    def test_second_at_ends_it(self):
        self.assertIsNone(find_mention("@ 甲@乙", 5))

    def test_only_looks_before_cursor(self):
        line = "@ 甲 会议记录"
        self.assertEqual(find_mention(line, 4), (0, "甲"))
        self.assertIsNone(find_mention(line, 9))   # 光标已经在标题文字后面了

    def test_empty_and_bad_col(self):
        self.assertIsNone(find_mention("", 0))
        self.assertIsNone(find_mention("@ ", 0))


class TestUrl(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(note_url("n1"), "note:n1")
        self.assertEqual(note_id_of_url(note_url("n1")), "n1")

    def test_not_a_note_link(self):
        self.assertIsNone(note_id_of_url("https://a.com"))
        self.assertIsNone(note_id_of_url(""))
        self.assertIsNone(note_id_of_url("note:"))

    def test_external_url_untouched(self):
        # 普通网址不能被误认成内链
        self.assertIsNone(note_id_of_url("note://x"))

    def test_mention_text(self):
        self.assertEqual(mention_text("会议记录"), "@会议记录")
        self.assertEqual(mention_text(""), "@")


class TestRank(unittest.TestCase):
    def test_empty_query_by_recency(self):
        cs = [cand("a", "甲", updated=1), cand("b", "乙", updated=3)]
        self.assertEqual([c["id"] for c in rank(cs, "")], ["b", "a"])

    def test_prefix_beats_contains(self):
        cs = [cand("mid", "甲乙丙"), cand("head", "乙丙丁")]
        self.assertEqual([c["id"] for c in rank(cs, "乙")], ["head", "mid"])

    def test_sub_matches_last(self):
        cs = [cand("s", "无关", sub="工作 / 会议")]
        self.assertEqual([c["id"] for c in rank(cs, "会议")], ["s"])

    def test_no_match_dropped(self):
        cs = [cand("a", "甲"), cand("b", "乙")]
        self.assertEqual(rank(cs, "丙"), [])

    def test_limit(self):
        cs = [cand(str(i), "笔记%d" % i, updated=i) for i in range(20)]
        self.assertEqual(len(rank(cs, "", limit=6)), 6)

    def test_no_default_limit(self):
        """回归：候选浮层改成 8 行视口 + 滚动条后，rank 默认不能再砍到 8 条。

        旧版 `limit=8` 会把第 9 篇起直接丢掉，滚动条再怎么翻也翻不出来
        （2026-11-06 用户报「弹框列表没有展示全部笔记」）。
        """
        cs = [cand(str(i), "笔记%d" % i, updated=i) for i in range(20)]
        self.assertEqual(len(rank(cs, "")), 20)
        self.assertEqual(len(rank(cs, "笔记")), 20)

    def test_case_insensitive(self):
        cs = [cand("a", "Meeting Notes")]
        self.assertEqual([c["id"] for c in rank(cs, "meeting")], ["a"])

    def test_recency_inside_same_score(self):
        cs = [cand("old", "会议", updated=1), cand("new", "会议x", updated=9)]
        # 两条都是「标题包含」，按最近修改排
        self.assertEqual([c["id"] for c in rank(cs, "会议")], ["new", "old"])

    def test_multi_word_is_and(self):
        """搜索框里可以写「工作 会议」：标题 + 文件夹路径合起来都得命中。"""
        cs = [cand("a", "周会纪要", sub="工作 / 会议"),
              cand("b", "周会纪要", sub="生活"),
              cand("c", "购物清单", sub="工作")]
        self.assertEqual([c["id"] for c in rank(cs, "周会 会议")], ["a"])
        self.assertEqual([c["id"] for c in rank(cs, "工作 纪要")], ["a"])

    def test_multi_word_all_must_hit(self):
        cs = [cand("a", "周会", sub="工作 / 会议")]
        self.assertEqual(rank(cs, "周会 不存在"), [])

    def test_multi_word_whitespace_only(self):
        cs = [cand("a", "甲", updated=1), cand("b", "乙", updated=2)]
        # 全是空格 = 没有查询词，退回最近修改
        self.assertEqual([c["id"] for c in rank(cs, "   ")], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
