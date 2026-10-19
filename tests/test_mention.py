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

    def test_after_text_needs_whitespace_before(self):
        self.assertEqual(find_mention("见 @ 会议", 6), (2, "会议"))

    def test_at_mid_word_is_not_trigger(self):
        # 邮箱 / 用户名里的 @ 左邻是字母数字，不算触发
        self.assertIsNone(find_mention("zhang@163.com", 13))
        self.assertIsNone(find_mention("a@b c", 6))

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

    def test_case_insensitive(self):
        cs = [cand("a", "Meeting Notes")]
        self.assertEqual([c["id"] for c in rank(cs, "meeting")], ["a"])

    def test_recency_inside_same_score(self):
        cs = [cand("old", "会议", updated=1), cand("new", "会议x", updated=9)]
        # 两条都是「标题包含」，按最近修改排
        self.assertEqual([c["id"] for c in rank(cs, "会议")], ["new", "old"])


if __name__ == "__main__":
    unittest.main()
