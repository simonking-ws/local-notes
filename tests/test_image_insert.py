# -*- coding: utf-8 -*-
"""图片插入端到端测试（真 Tk 窗口 + 临时库）。

盯的无非是这条链：**敲一行 ![alt](path) → 回车 → 正文出现图片 →
serialize 认领得回来 → 备份往返不丢 → 导出 Markdown 是 ![](src)**。

⚠️ 本测试用临时库：所有图片落进临时 BASE，绝不碰真实 data/。
多 Tk 实例的图片缓存污染是已知问题（见项目 MEMORY），所以这里在 setUpClass
里只建**一个** Tk，全程复用。
"""
import os
import sys
import shutil
import struct
import tempfile
import tkinter as tk
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import imgstore                                             # noqa: E402
import backup                                               # noqa: E402
import exporter                                             # noqa: E402


def make_png(w, h):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class ImageEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="sj_img_e2e_")
        self.src_file = os.path.join(self.base, "src.png")
        with open(self.src_file, "wb") as f:
            f.write(make_png(8, 6))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_full_chain(self):
        # 1) 造块 + 存文件
        with open(self.src_file, "rb") as f:
            block = imgstore.make_block(f.read(), alt="红块", base=self.base)
        self.assertEqual(block[0], "img")
        self.assertEqual(block[1]["alt"], "红块")
        self.assertEqual((block[1]["w"], block[1]["h"]), (8, 6))
        self.assertTrue(os.path.isfile(imgstore.src_path(block[1]["src"],
                                                         base=self.base)))

        # 2) 真窗口渲染：image_create 不报错、PhotoImage 保活
        txt = tk.Text(self.root)
        photo = tk.PhotoImage(file=imgstore.src_path(block[1]["src"], base=self.base))
        txt.image_create("1.0", image=photo, name="img:" + block[1]["id"])
        names = [n for n in txt.image_names()]
        self.assertIn("img:" + block[1]["id"], names)
        txt.destroy()

        # 3) serialize 往返（模拟编辑器里 dump 的 image 分支）
        content = [["t", "前\n", []], block, ["t", "后", []]]
        text, spans, blocks = backup.content_to_spans(content)
        back = backup.spans_to_runs(text, spans, blocks)
        self.assertEqual(back, content)

        # 4) Markdown 导出
        md_blocks = exporter.blocks_from_content(content)
        md = exporter.markdown_text(md_blocks)
        self.assertIn("![红块](%s)" % block[1]["src"], md)

    def test_gc_keeps_referenced(self):
        with open(self.src_file, "rb") as f:
            block = imgstore.make_block(f.read(), alt="x", base=self.base)
        notes = [{"content": [block]}]
        removed = imgstore.gc_orphans(notes, base=self.base)
        self.assertEqual(removed, [])
        self.assertTrue(os.path.isfile(imgstore.src_path(block[1]["src"],
                                                         base=self.base)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
