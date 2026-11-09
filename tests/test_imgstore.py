# -*- coding: utf-8 -*-
"""图片落盘层测试（标准库 unittest）。

盯的是「图片块能不能原样落库、重新打开、再导出」这条链上最容易悄悄坏的地方：

  * PNG 尺寸从 IHDR 读对（不依赖 Pillow）；
  * 同一张图重复插入只占一个文件（hash 去重）；
  * src 只存相对路径，且 ``src_path`` 拒绝逃出 media/ 的脏路径；
  * 整行 Markdown 图片语法解析（含带空格路径的尖括号写法）；
  * 备份 blocks 往返不丢字段、校验和覆盖 src；
  * 孤儿回收只删没人引用的图，且图床 URL 不当本地文件读。

运行：
    python -m unittest discover -s tests -p "test_imgstore.py" -v
"""
import os
import sys
import shutil
import struct
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import imgstore                                              # noqa: E402
import backup                                                # noqa: E402
import base64                                                # noqa: E402


def make_png(w, h, rgba=(255, 0, 0, 255)):
    """用标准库造一张纯色 PNG（不依赖 Pillow，测试才能零依赖跑）。"""
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = b""
    for _ in range(h):
        raw += b"\x00" + bytes(rgba) * w
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class PngTest(unittest.TestCase):
    def test_is_png_and_size(self):
        data = make_png(7, 5)
        self.assertTrue(imgstore.is_png(data))
        self.assertEqual(imgstore.png_size(data), (7, 5))
        self.assertEqual(imgstore.png_size(b"not a png"), (0, 0))


class SaveImageTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="sj_img_")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_save_and_dedup(self):
        data = make_png(4, 3)
        a = imgstore.save_image(data, base=self.base)
        b = imgstore.save_image(data, base=self.base)
        self.assertEqual(a["src"], b["src"])
        self.assertTrue(a["src"].startswith("media/"))
        self.assertTrue(a["src"].endswith(".png"))
        self.assertEqual((a["w"], a["h"]), (4, 3))
        self.assertEqual(len(os.listdir(os.path.join(self.base, "media"))), 1)

    def test_src_path_rejects_escape(self):
        self.assertIsNone(imgstore.src_path("https://x/y.png", base=self.base))
        self.assertIsNone(imgstore.src_path("media/../../notes_data.db",
                                            base=self.base))
        self.assertIsNone(imgstore.src_path("notes_data.json", base=self.base))
        d = imgstore.save_image(make_png(2, 2), base=self.base)
        self.assertTrue(os.path.isfile(imgstore.src_path(d["src"], base=self.base)))

    def test_remove_local(self):
        d = imgstore.save_image(make_png(2, 2), base=self.base)
        p = imgstore.src_path(d["src"], base=self.base)
        self.assertTrue(os.path.isfile(p))
        self.assertTrue(imgstore.remove_local(d["src"], base=self.base))
        self.assertFalse(os.path.isfile(p))
        self.assertFalse(imgstore.remove_local(d["src"], base=self.base))


class MarkdownSyntaxTest(unittest.TestCase):
    def test_parse_whole_line(self):
        self.assertEqual(imgstore.parse_md_image("![截图](media/a.png)"),
                         ("截图", "media/a.png"))
        self.assertEqual(imgstore.parse_md_image("  ![](https://x/y.png)  "),
                         ("", "https://x/y.png"))
        self.assertEqual(imgstore.parse_md_image("![含空格](<path to/a.png>)"),
                         ("含空格", "path to/a.png"))

    def test_inline_is_not_parsed(self):
        # 用户拍板：首版只做整行，行内混排不认
        self.assertIsNone(imgstore.parse_md_image("前文 ![x](y.png) 后文"))
        self.assertIsNone(imgstore.parse_md_image("![x](y) 尾巴"))

    def test_format_roundtrip(self):
        blk = ["img", {"id": "i_1", "src": "media/a.png", "w": 1, "h": 1,
                       "alt": "说明"}]
        self.assertEqual(imgstore.format_md_image(blk), "![说明](media/a.png)")
        self.assertEqual(imgstore.parse_md_image(imgstore.format_md_image(blk)),
                         ("说明", "media/a.png"))


class BackupRoundTripTest(unittest.TestCase):
    def test_img_block_in_blocks(self):
        content = [["t", "前\n", []],
                   ["img", {"id": "i_9", "src": "media/a.png", "w": 640,
                            "h": 480, "alt": "截图"}],
                   ["t", "后", []]]
        text, spans, blocks = backup.content_to_spans(content)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["kind"], "img")
        back = backup.spans_to_runs(text, spans, blocks)
        self.assertEqual(back, content)

    def test_checksum_covers_src(self):
        payload = {"format": "sujian.backup", "schema": 2,
                   "folders": [], "notes": []}
        good = backup.encode(payload)
        h = backup.digest(good)
        good["notes"] = [{"id": "n1", "content": "x",
                          "blocks": [{"i": 0, "kind": "img",
                                      "src": "media/a.png"}]}]
        self.assertNotEqual(backup.digest(good), h,
                            "改了 img 的 src 必须让校验和变化")


class BackupMediaTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="sj_bmedia_")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_media_roundtrip(self):
        a = imgstore.save_image(make_png(3, 2), base=self.base)
        notes = [{"content": [["img", {"src": a["src"]}]]}]
        media = backup.pack_media(notes, base=self.base)
        self.assertEqual(list(media), [a["src"]])
        self.assertEqual(base64.b64decode(media[a["src"]]),
                         imgstore.read_local(a["src"], base=self.base))
        # 删掉本地文件，模拟换机后导入
        imgstore.remove_local(a["src"], base=self.base)
        self.assertEqual(backup.unpack_media(media, base=self.base), 1)
        self.assertTrue(os.path.isfile(imgstore.src_path(a["src"], base=self.base)))

    def test_remote_not_packed(self):
        notes = [{"content": [["img", {"src": "https://x/y.png"}]]}]
        self.assertEqual(backup.pack_media(notes, base=self.base), {})

    def test_checksum_covers_media(self):
        p = backup.encode({}, extra={"media": {"media/a.png": "AAAA"}})
        d = backup.digest(p)
        p["media"]["media/a.png"] = "BBBB"
        self.assertNotEqual(backup.digest(p), d)


class GcTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="sj_gc_")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_only_orphans_removed(self):
        keep = imgstore.save_image(make_png(2, 2), base=self.base)
        orphan = imgstore.save_image(make_png(3, 3, (0, 255, 0, 255)),
                                     base=self.base)
        notes = [{"content": [["img", {"src": keep["src"]}]]}]
        removed = imgstore.gc_orphans(notes, base=self.base)
        self.assertEqual(removed, [orphan["src"]])
        self.assertTrue(os.path.isfile(imgstore.src_path(keep["src"], base=self.base)))
        self.assertFalse(os.path.isfile(imgstore.src_path(orphan["src"], base=self.base)))

    def test_remote_src_not_counted_as_file(self):
        notes = [{"content": [["img", {"src": "https://x/y.png"}]]}]
        d = imgstore.save_image(make_png(2, 2), base=self.base)
        self.assertEqual(imgstore.collect_refs(notes), set())
        self.assertIn(d["src"], imgstore.gc_orphans(notes, base=self.base))

    def test_recycle_single_note_keeps_image(self):
        # 单条笔记删除：payload 就是一条 note（有 content）。早先只认 p["notes"]
        # 会漏掉它 → GC 把图删了，还原后空图。
        d = imgstore.save_image(make_png(2, 2), base=self.base)
        recs = [{"kind": "note", "payload": {"content": [["img", {"src": d["src"]}]]}}]
        refs = imgstore.collect_refs(imgstore.notes_from_recycle(recs))
        self.assertIn(d["src"], refs)
        self.assertEqual(imgstore.gc_orphans([], self.base, keep=refs), [])

    def test_recycle_folder_subtree_keeps_image(self):
        d = imgstore.save_image(make_png(2, 2), base=self.base)
        recs = [{"kind": "folder", "payload": {
            "folders": [{"id": "f1"}],
            "notes": [{"content": [["img", {"src": d["src"]}]]}],
        }}]
        refs = imgstore.collect_refs(imgstore.notes_from_recycle(recs))
        self.assertIn(d["src"], refs)

    def test_recycle_bad_payload_is_ignored(self):
        # 脏数据不能让整个 GC 崩掉
        recs = [{"kind": "note", "payload": None},
                {"kind": "note", "payload": "not a dict"},
                {"kind": "note"}]
        self.assertEqual(imgstore.notes_from_recycle(recs), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
