# -*- coding: utf-8 -*-
"""版本号比较的单测。

这个文件的重点是**两类容易翻车的情况**，比「1.0 < 1.1」这种显然的用例重要得多：

- **补齐位数**：线上最早的 release tag 是 ``1.0``（两位），而代码里写 ``1.0.0``
  （三位）。Python 里 ``(1, 0) < (1, 0, 0)`` 恒为 True，不补零的话就是
  「装的是最新版，却每次启动都提示有新版本」——而且用户点了更新也没用，
  因为更新完还是同样的判断。这种 bug 极难自查，只能靠测试钉住。
- **解析不出来时宁可漏报**：``is_newer`` 拿不准一律 False。误报的代价是
  每次都被骚扰；漏报的代价是用户自己去 release 页看一眼。

    python tests\\test_version.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import version


class TestAsTuple(unittest.TestCase):
    def test_pads_to_four(self):
        # 两位 tag 与三位版本号必须能直接比 —— 补零到等长
        self.assertEqual(version.as_tuple("1.0"), version.as_tuple("1.0.0"))
        self.assertEqual(version.as_tuple("1.0"), (1, 0, 0, 0))
        self.assertEqual(version.as_tuple("2"), (2, 0, 0, 0))

    def test_strips_v_prefix(self):
        # 仓库里的 tag 有 v 前缀和没有前缀的都有
        self.assertEqual(version.as_tuple("v1.2.3"), version.as_tuple("1.2.3"))

    def test_drops_prerelease_suffix(self):
        # -beta.1 / +build 不参与「谁更新」的比较
        self.assertEqual(version.as_tuple("1.1.0-beta.1"), version.as_tuple("1.1.0"))
        self.assertEqual(version.as_tuple("1.1.0+build9"), version.as_tuple("1.1.0"))

    def test_junk_does_not_raise(self):
        # 作者手滑写了个 "1.1b" 也不能让后台检查抛异常
        for dirty in ("", "  ", "v", "abc", "1.1b", None):
            self.assertEqual(len(version.as_tuple(dirty)), 4)

    def test_numeric_order_not_lexical(self):
        # 1.10 比 1.9 新：按字符串比会得到相反答案
        self.assertTrue(version.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(version.is_newer("1.9.9", "1.10.0"))

    def test_more_segments(self):
        # 四段也要能比（Windows 的文件版本号就是四段）
        self.assertTrue(version.is_newer("1.0.0.1", "1.0.0"))


class TestIsNewer(unittest.TestCase):
    def test_equal_is_not_newer(self):
        self.assertFalse(version.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(version.is_newer("v1.0", "1.0.0"))   # ← 补零那条

    def test_older_is_not_newer(self):
        self.assertFalse(version.is_newer("0.9.9", "1.0.0"))

    def test_garbage_remote_is_not_newer(self):
        # 拿不准就漏报，绝不误报
        self.assertFalse(version.is_newer("", "1.0.0"))
        self.assertFalse(version.is_newer("abc", "1.0.0"))

    def test_defaults_to_own_version(self):
        # local 省略时用 __version__ 自己比，相等 → False
        self.assertFalse(version.is_newer(version.__version__))


if __name__ == "__main__":
    unittest.main(verbosity=2)
