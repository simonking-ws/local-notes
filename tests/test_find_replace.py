# -*- coding: utf-8 -*-
"""查找替换的纯逻辑：命中区间不重叠、执行顺序必须从后往前。

为什么值得单独钉：`Ctrl+H` 全部替换时，一处 `delete + insert` 会改变正文长度。
从前往后改，后面所有 Text 索引就全指到别的字符上了 —— 界面上表现为「替换了
一半、剩下的词被啃掉几个字」，而且只有命中多、替换串和查找串长度不同才看得出来。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

from find_replace import find_spans, replacement_plan, apply_plan, replace_all  # noqa: E402


class FindSpansTest(unittest.TestCase):
    def test_empty_query_has_no_hit(self):
        self.assertEqual(find_spans("abc", ""), [])

    def test_plain_hits(self):
        self.assertEqual(find_spans("a cat and a cat", "cat"), [(2, 5), (12, 15)])

    def test_hits_never_overlap(self):
        """aaaa 找 aa 是两处，不是三处 —— 与 Tk 的 text.search 同口径。"""
        self.assertEqual(find_spans("aaaa", "aa"), [(0, 2), (2, 4)])

    def test_nocase_matches_tk_search(self):
        """Ctrl+F 用的是 nocase=True，替换的命中口径必须和它一致。"""
        self.assertEqual(find_spans("Cat CAT cat", "cat"), [(0, 3), (4, 7), (8, 11)])
        self.assertEqual(find_spans("Cat CAT cat", "cat", nocase=False), [(8, 11)])

    def test_cjk(self):
        self.assertEqual(find_spans("下雨的约定，约定", "约定"), [(3, 5), (6, 8)])

    def test_limit_is_a_hard_cap(self):
        self.assertEqual(len(find_spans("a" * 20, "a", limit=3)), 3)


class ReplacementPlanTest(unittest.TestCase):
    def test_plan_is_reversed(self):
        """倒序是硬要求：先改后面的，前面的索引才不会漂移。"""
        plan = replacement_plan([(0, 1), (3, 4), (7, 8)], "X")
        self.assertEqual([p[0] for p in plan], [7, 3, 0])

    def test_plan_carries_replacement(self):
        self.assertEqual(replacement_plan([(0, 2)], "YY"), [(0, 2, "YY")])

    def test_empty_replacement_is_deletion(self):
        self.assertEqual(replacement_plan([(0, 2)], ""), [(0, 2, "")])

    def test_overlapping_spans_keep_only_one(self):
        """重叠区间只留靠后的一处，不能对同一段字符 delete 两次。"""
        self.assertEqual(replacement_plan([(0, 3), (2, 5)], "X"), [(2, 5, "X")])

    def test_invalid_spans_are_skipped(self):
        self.assertEqual(replacement_plan([(5, 5), (1, 2)], "X"), [(1, 2, "X")])

    def test_empty_plan(self):
        self.assertEqual(replacement_plan([], "X"), [])


class ReplaceAllTest(unittest.TestCase):
    def test_equal_length(self):
        self.assertEqual(replace_all("a cat and a cat", "cat", "dog"),
                         ("a dog and a dog", 2))

    def test_longer_replacement(self):
        """替换串更长：这条最能证明倒序的必要性。"""
        self.assertEqual(replace_all("a a a", "a", "long"),
                         ("long long long", 3))

    def test_shorter_and_empty_replacement(self):
        self.assertEqual(replace_all("aaa", "aa", "x"), ("xa", 1))
        self.assertEqual(replace_all("a-b-a", "-", ""), ("aba", 2))

    def test_replacement_text_is_literal(self):
        """不区分大小写地找，但替换串按字面写，不自动保留原大小写。"""
        self.assertEqual(replace_all("Cat CAT", "cat", "dog"), ("dog dog", 2))

    def test_no_hit_returns_original(self):
        self.assertEqual(replace_all("abc", "z", "x"), ("abc", 0))

    def test_apply_plan_matches_replace_all(self):
        text = "one one one"
        spans = find_spans(text, "one")
        plan = replacement_plan(spans, "1")
        self.assertEqual(apply_plan(text, plan), "1 1 1")
        self.assertEqual(len(plan), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
