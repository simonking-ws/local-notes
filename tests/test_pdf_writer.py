# -*- coding: utf-8 -*-
"""PDF 文件本体的结构自测（纯函数，不需要 Windows / GDI）。

`build_pdf()` 收的就是几页现成的 RGB 位图，所以这里可以喂手工造的小位图，
再把生成出来的字节按 PDF 语法逐条校验：xref 偏移对不对、每个对象的
/Length 与流实际长度是否一致、页面数、结尾 %%EOF。
"""
import os
import re
import sys
import zlib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import pdf_export


def make_page(w=8, h=4, color=(200, 30, 30)):
    return (w, h, bytes(color) * (w * h))


class TestStructure(unittest.TestCase):
    def build(self, n=2):
        pages = [make_page(color=(200, 30, 30)) for _ in range(n)]
        return pdf_export.build_pdf(pages, title="测试标题")

    def test_header_and_eof(self):
        data = self.build()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))

    def test_xref_offsets_point_at_objects(self):
        data = self.build()
        start = int(re.search(rb"startxref\s+(\d+)", data).group(1))
        self.assertEqual(data[start:start + 4], b"xref")
        body = data[start:]
        rows = re.findall(rb"(\d{10}) (\d{5}) ([nf]) \n", body)
        # 第一条是自由项，其余每个都要精确落在「N 0 obj」上
        self.assertEqual(rows[0][2], b"f")
        for i, (off, _gen, kind) in enumerate(rows[1:], start=1):
            pos = int(off)
            self.assertEqual(data[pos:pos + len(b"%d 0 obj" % i)],
                             b"%d 0 obj" % i, "对象 %d 的偏移不对" % i)

    def test_object_count_matches_size(self):
        data = self.build(3)
        size = int(re.search(rb"/Size (\d+)", data).group(1))
        start = int(re.search(rb"startxref\s+(\d+)", data).group(1))
        # xref 里每条 "n" 记录都有一份；末尾那条是自由项，所以 +1
        self.assertEqual(size, data[start:].count(b" 00000 n \n") + 1)

    def test_pages_count(self):
        data = self.build(3)
        self.assertIn(b"/Count 3", data)
        self.assertEqual(data.count(b"/Type /Page "), 3)

    def test_stream_lengths_are_exact(self):
        data = self.build(2)
        for m in re.finditer(rb"/Length (\d+) >>\nstream\n", data):
            n = int(m.group(1))
            start = m.end()
            tail = data[start + n:start + n + 12]
            self.assertTrue(tail.lstrip(b"\n").startswith(b"endstream"),
                            "流长度和实际内容对不上: %r" % tail)

    def test_image_stream_is_flate_rgb(self):
        page = make_page(4, 2, (10, 20, 30))
        data = pdf_export.build_pdf([page], title="x")
        self.assertIn(b"/ColorSpace /DeviceRGB", data)
        self.assertIn(b"/Filter /FlateDecode", data)
        self.assertIn(b"/Width 4 /Height 2", data)
        m = re.search(rb"/Length (\d+) >>\nstream\n", data)
        n = int(m.group(1))
        raw = zlib.decompress(data[m.end():m.end() + n])
        self.assertEqual(len(raw), 4 * 2 * 3)
        self.assertEqual(raw[:3], bytes((10, 20, 30)))

    def test_page_media_box(self):
        data = self.build(1)
        box = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)
        self.assertAlmostEqual(float(box.group(1)), 595.28, places=1)
        self.assertAlmostEqual(float(box.group(2)), 841.89, places=1)

    def test_chinese_title_is_utf16(self):
        data = pdf_export.build_pdf([make_page()], title="中文标题")
        # PDF 的文本串放中文必须写成带 BOM 的 UTF-16BE 十六进制串
        self.assertIn(b"/Title <FEFF", data)
        hexed = data.split(b"/Title <FEFF")[1].split(b">")[0]
        self.assertEqual(bytes.fromhex(hexed.decode()).decode("utf-16-be"), "中文标题")

    def test_empty_pages(self):
        # 一条没有任何内容的笔记：不能崩，也不该产出一个 0 页的非法 PDF
        data = pdf_export.build_pdf([make_page()], title="空")
        self.assertIn(b"/Count 1", data)


class TestColorHelpers(unittest.TestCase):
    def test_rgb_short_and_bad(self):
        self.assertEqual(pdf_export._rgb("#fff"), (255, 255, 255))
        self.assertEqual(pdf_export._rgb("#123456"), (0x12, 0x34, 0x56))
        self.assertEqual(pdf_export._rgb("", (1, 2, 3)), (1, 2, 3))
        self.assertEqual(pdf_export._rgb("zzz", (1, 2, 3)), (1, 2, 3))

    def test_mix(self):
        self.assertEqual(pdf_export._mix((0, 0, 0), (255, 255, 255), 0.5), (128, 128, 128))
        self.assertEqual(pdf_export._mix((0, 0, 0), (255, 255, 255), 0), (0, 0, 0))

    def test_colorref_order(self):
        # COLORREF 是 0x00BBGGRR，写反了整个 PDF 的红蓝会互换
        self.assertEqual(pdf_export._colorref((1, 2, 3)), 3 << 16 | 2 << 8 | 1)


class TestOpts(unittest.TestCase):
    def test_make_opts_basic(self):
        pal = {"text": "#1f1f24", "soft": "#76767e", "accent": "#2f7cf6",
               "link": "#1668d9", "green": "#16a34a"}
        o = pdf_export.make_opts(pal, 12, "Microsoft YaHei", "Consolas")
        self.assertEqual(o["left"], pdf_export.MARGIN_PT)
        self.assertAlmostEqual(o["right"], pdf_export.PAGE_W_PT - pdf_export.MARGIN_PT)
        self.assertAlmostEqual(o["width"], pdf_export.PAGE_W_PT - 2 * pdf_export.MARGIN_PT)
        self.assertEqual(o["body"], 12.0)
        # 底色的明度：代码块比行内代码深一档，但都比纸白深一点点
        self.assertLess(o["code_bg"][0], 255)
        self.assertLessEqual(o["code_bg"][0], o["icode_bg"][0])


if __name__ == "__main__":
    unittest.main()


class TestObjectGraph(unittest.TestCase):
    """按 PDF 的对象图走一遍：Catalog → Pages → Page → Contents / XObject。

    偏移正确不代表引用正确 —— 少写一个「0 R」或者引错对象号，阅读器会直接
    报「文件已损坏」。这里逐个引用做存在性检查。
    """

    def setUp(self):
        self.data = pdf_export.build_pdf([make_page(), make_page()], title="图")

    def objs(self):
        out = {}
        for m in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj", self.data, re.S):
            out[int(m.group(1))] = m.group(2)
        return out

    def test_walk(self):
        objs = self.objs()
        catalog = int(re.search(rb"/Root (\d+) 0 R", self.data).group(1))
        self.assertIn(b"/Type /Catalog", objs[catalog])
        pages = int(re.search(rb"/Pages (\d+) 0 R", objs[catalog]).group(1))
        self.assertIn(b"/Type /Pages", objs[pages])
        kids = [int(x) for x in re.findall(rb"(\d+) 0 R",
                                           objs[pages].split(b"/Kids [")[1].split(b"]")[0])]
        self.assertEqual(len(kids), 2)
        for kid in kids:
            body = objs[kid]
            self.assertIn(b"/Type /Page ", body)
            self.assertIn(b"/Parent %d 0 R" % pages, body)
            cont = int(re.search(rb"/Contents (\d+) 0 R", body).group(1))
            xobj = int(re.search(rb"/XObject << /Im0 (\d+) 0 R >>", body).group(1))
            self.assertIn(b"stream", objs[cont])
            self.assertIn(b"/Subtype /Image", objs[xobj])

    def test_info_exists(self):
        objs = self.objs()
        info = int(re.search(rb"/Info (\d+) 0 R", self.data).group(1))
        self.assertIn(b"/Title", objs[info])
