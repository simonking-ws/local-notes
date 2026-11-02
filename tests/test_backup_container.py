# -*- coding: utf-8 -*-
"""导出备份的编码容器 / 可选口令加密（标准库 unittest，不建 Tk 解释器）。

盯的是这几条，任何一条松了都会「文件看着还在、内容却回不来」：

  * 空口令 = 编码态：默认导出 `.json.gz`（真 gzip 文件），解压后打开也看不到
    笔记正文，导入时 backup.load 自动剥壳还原，用户零感知、无需口令；
  * 非空口令 = 加密态：错口令 / 没口令 / 文件被改都要硬拦，且不能给区分线索；
  * 容器只是「一层壳」：还原出来的仍是完整的 schema 2 快照，encode / decode /
    overwrite / merge 一个字都不用改；旧明文 / gzip 备份照旧能导；
  * 识别按文件内容不按后缀：改成 .txt 也认得。

运行：
    python tests\\test_backup_container.py
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backup                                                    # noqa: E402


SECRET_TEXT = "绝密会议纪要ABC"


def sample():
    return {
        "folders": [{"id": "f_work", "name": "工作"},
                    {"id": "f_sub", "name": "子目录", "parent": "f_work"}],
        "notes": [
            {"id": "n1", "folder": "f_work",
             "content": [["t", SECRET_TEXT, []], ["t", "\n第二行", []]],
             "created": 1000.0, "updated": 2000.0, "pinned": True},
            {"id": "n2", "folder": "f_sub",
             "content": [["t", "另一篇", []]],
             "created": 1100.0, "updated": 2100.0},
        ],
        "settings": {"body_size": 15, "font_family": "",
                     "toolbar": {"hidden": ["bold"]},
                     "tabs": ["n1"], "side_w": 260},
        "styles": {"c_ff0000": {"fg": "#ff0000"}},
    }


class ContainerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian-container-")
        self.payload = backup.encode(
            sample(), exported_at="2026-11-06T18:00:00+08:00")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def read_blob(self, p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_blob(self, p, blob):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

    def read_gz_blob(self, p):
        with gzip.open(p, "rb") as f:
            return json.loads(f.read().decode("utf-8"))

    def write_gz_blob(self, p, blob):
        with open(p, "wb") as f:
            with gzip.GzipFile(filename="", fileobj=f, mode="wb",
                               compresslevel=6, mtime=0) as gz:
                gz.write(json.dumps(blob, ensure_ascii=False).encode("utf-8"))

    def write_container(self, name, password=""):
        p = self.path(name)
        backup.dump_container(p, self.payload, password=password)
        return p

    def assert_restored(self, raw):
        """还原出来的必须是一份完整、校验和正确的 schema 2 快照。"""
        d, warn = backup.decode(raw)
        self.assertEqual(warn, [], "内层 checksum 不该报警")
        self.assertEqual(len(d["notes"]), 2)
        self.assertEqual(len(d["folders"]), 2)
        self.assertEqual(d["prefs"]["body_size"], 15)
        self.assertEqual(d["prefs"]["toolbar"], {"hidden": ["bold"]})
        self.assertEqual(d["styles"], {"c_ff0000": {"fg": "#ff0000"}})
        self.assertNotIn("tabs", d["prefs"])
        self.assertEqual(backup.content_to_spans(d["notes"][0]["content"])[0],
                         SECRET_TEXT + "\n第二行")
        return d

    # ---------- 编码态：默认、无口令、自动解码 ----------

    def test_empty_password_is_coded_and_auto_decodes(self):
        p = self.write_container("coded.json")
        blob = self.read_blob(p)
        self.assertEqual(blob["format"], backup.CONTAINER_FORMAT)
        self.assertEqual(blob["mode"], "coded")
        self.assertEqual(blob["iter"], 0)
        self.assert_restored(backup.load(p))            # 不传任何口令

    def test_none_password_is_same_as_empty(self):
        p = self.write_container("none.json", password=None)
        self.assertEqual(self.read_blob(p)["mode"], "coded")
        self.assert_restored(backup.load(p))

    def test_file_shows_no_plaintext(self):
        """这是需求本身：文本编辑器打开也读不到笔记内容。"""
        p = self.write_container("hidden.json")
        with open(p, "rb") as f:
            raw = f.read()
        for text in (SECRET_TEXT, "第二行", "另一篇", "工作", "子目录",
                     "body_size", "c_ff0000"):
            self.assertNotIn(text.encode("utf-8"), raw,
                             "导出文件里漏出了明文：%s" % text)
        # 也不是「把明文 JSON 直接 base64 一下」那种一眼看穿的编码
        blob = self.read_blob(p)
        self.assertNotIn(self.payload["checksum"], json.dumps(blob))
        self.assertTrue(blob["data"])
        self.assertTrue(blob["tag"])

    def test_container_shape(self):
        blob = backup.seal_payload(self.payload)
        self.assertEqual(set(blob), {"format", "container", "mode", "iter",
                                    "salt", "nonce", "data", "tag"})
        self.assertEqual(blob["container"], backup.CONTAINER_VERSION)
        self.assertEqual(len(backup._b64d(blob["salt"])), backup._SALT_LEN)
        self.assertEqual(len(backup._b64d(blob["nonce"])), backup._NONCE_LEN)

    def test_each_export_uses_fresh_salt_and_nonce(self):
        """随机 salt/nonce 让两次导出字节不同，但两次都必须能还原。"""
        a = self.write_container("a.json")
        b = self.write_container("b.json")
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertNotEqual(f1.read(), f2.read())
        self.assert_restored(backup.load(a))
        self.assert_restored(backup.load(b))

    def test_recognition_by_content_not_suffix(self):
        p = self.write_container("renamed.txt")
        self.assert_restored(backup.load(p))

    def test_container_is_refused_by_decode_directly(self):
        """没剥壳就丢给 decode() 必须明确拒绝，而不是含糊地报缺字段。"""
        with self.assertRaises(backup.BackupError):
            backup.decode(backup.seal_payload(self.payload))

    # ---------- 加密态：口令可选但必须正确 ----------

    def test_sealed_roundtrip(self):
        p = self.write_container("sealed.json", password="correct horse")
        blob = self.read_blob(p)
        self.assertEqual(blob["mode"], "sealed")
        self.assertEqual(blob["iter"], backup.CONTAINER_ITERATIONS)
        self.assert_restored(backup.load(p, password="correct horse"))

    def test_sealed_without_password_raises_need_password(self):
        p = self.write_container("sealed.json", password="pw")
        with self.assertRaises(backup.NeedPassword):
            backup.load(p)
        self.assertTrue(issubclass(backup.NeedPassword, backup.BackupError))

    def test_sealed_wrong_password_is_refused(self):
        p = self.write_container("sealed.json", password="pw")
        for bad in ("", "PW", "pw ", "other"):
            with self.assertRaises(backup.BackupError):
                backup.load(p, password=bad)

    def test_coded_ignores_stray_password(self):
        """编码态无论传什么口令都照常还原（它没有秘密可言）。"""
        p = self.write_container("coded.json")
        self.assert_restored(backup.load(p, password="随便什么"))

    # ---------- 认证：先验 tag，改一个字节都不行 ----------

    def _tamper(self, name, **changes):
        p = self.write_container(name)
        blob = self.read_blob(p)
        blob.update(changes)
        self.write_blob(p, blob)
        return p

    def test_tamper_data_is_refused(self):
        p = self.write_container("t.json")
        blob = self.read_blob(p)
        s = blob["data"]
        blob["data"] = ("A" if s[0] != "A" else "B") + s[1:]
        self.write_blob(p, blob)
        with self.assertRaises(backup.BackupError):
            backup.load(p)

    def test_tamper_header_fields_is_refused(self):
        """mode 翻转、iter 调小、版本改旧、salt/nonce 换掉，全都被 tag 或闸门抓住。"""
        for key, val in (("mode", "sealed"),
                         ("iter", 100000),
                         ("container", 0),
                         ("salt", backup._b64e(b"0" * backup._SALT_LEN)),
                         ("nonce", backup._b64e(b"0" * backup._NONCE_LEN))):
            p = self._tamper("t-%s.json" % key, **{key: val})
            with self.assertRaises(backup.BackupError):
                # 给个无关口令，保证不会被 NeedPassword 提前拦下、能真正走到验 tag
                backup.load(p, password="pw")

    def test_format_downgrade_is_refused_by_decode(self):
        """把容器伪装成普通快照，load 不剥壳，但 decode 一定会拒（缺 folders/notes）。"""
        p = self._tamper("downgrade.json", format=backup.FORMAT)
        with self.assertRaises(backup.BackupError):
            backup.decode(backup.load(p))

    def test_iter_out_of_range_is_refused_before_kdf(self):
        """头里的迭代数在验 tag 之前可读：改到超大值不能把导入卡死。"""
        p = self.write_container("sealed.json", password="pw")
        blob = self.read_blob(p)
        blob["iter"] = 10 ** 9
        self.write_blob(p, blob)
        with self.assertRaises(backup.BackupError):
            backup.load(p, password="pw")

    def test_truncated_data_is_refused(self):
        p = self.write_container("t.json")
        blob = self.read_blob(p)
        blob["data"] = blob["data"][:len(blob["data"]) // 2]
        self.write_blob(p, blob)
        with self.assertRaises(backup.BackupError):
            backup.load(p)

    # ---------- .json.gz：导出默认文件格式 ----------
    # 导出文件名沿用老版本的 `.json.gz`，所以整份文件必须是真 gzip；里面装的
    # 才是容器 JSON。load() 靠魔数 gunzip，再靠 format 认容器 —— 旧版 `.json.gz`
    # （gzip 里直接是明文快照 JSON）共用一个后缀，也不会串。

    def test_default_container_is_real_gzip_file(self):
        p = self.write_container("素笺备份_20261106-1830.json.gz")
        with open(p, "rb") as f:
            raw = f.read()
        self.assertEqual(raw[:2], b"\x1f\x8b", ".json.gz 必须是真的 gzip 文件")
        blob = self.read_gz_blob(p)
        self.assertEqual(blob["format"], backup.CONTAINER_FORMAT)
        self.assertEqual(blob["mode"], "coded")
        self.assert_restored(backup.load(p))

    def test_gzip_container_hides_plaintext_even_after_gunzip(self):
        """解压后看到的也只是容器头 + 一串 data，不是笔记正文。"""
        p = self.write_container("hidden.json.gz")
        with gzip.open(p, "rb") as f:
            inner = f.read()
        for text in (SECRET_TEXT, "第二行", "另一篇", "工作", "子目录",
                     "body_size", "c_ff0000"):
            self.assertNotIn(text.encode("utf-8"), inner,
                             "解压后漏出了明文：%s" % text)
        self.assert_restored(backup.load(p))

    def test_gzip_container_recognized_by_magic_not_suffix(self):
        p = self.write_container("export.json.gz")
        renamed = self.path("renamed.bin")
        shutil.copyfile(p, renamed)
        self.assert_restored(backup.load(renamed))

    def test_sealed_gzip_container_still_needs_password(self):
        p = self.write_container("sealed.json.gz", password="pw")
        with open(p, "rb") as f:
            self.assertEqual(f.read(2), b"\x1f\x8b")
        with self.assertRaises(backup.NeedPassword):
            backup.load(p)
        with self.assertRaises(backup.BackupError):
            backup.load(p, password="wrong")
        self.assert_restored(backup.load(p, password="pw"))

    def test_tamper_gzip_container_header_is_refused(self):
        """外层 gzip 里的头字段被改，tag 一样要拦住，不能因为多包一层就漏验。"""
        p = self.write_container("t.json.gz")
        blob = self.read_gz_blob(p)
        blob["iter"] = 100000
        self.write_gz_blob(p, blob)
        with self.assertRaises(backup.BackupError):
            backup.load(p)

    def test_old_and_new_gzip_share_suffix_without_collision(self):
        """同名后缀两种内容：旧明文快照 / 新容器，load() 都要各自走对分支。"""
        old, new = self.path("old.json.gz"), self.path("new.json.gz")
        backup.dump(old, self.payload)
        backup.dump_container(new, self.payload)
        with gzip.open(old, "rb") as f:
            self.assertNotIn(b"sujian.backup.container", f.read())
        self.assertEqual(self.read_gz_blob(new)["format"],
                         backup.CONTAINER_FORMAT)
        self.assert_restored(backup.load(old))
        self.assert_restored(backup.load(new))

    # ---------- 兼容性：旧格式 / 内部快照不走容器 ----------

    def test_legacy_plain_and_gzip_still_load(self):
        for name in ("old.json", "old.json.gz"):
            p = self.path(name)
            backup.dump(p, self.payload)
            self.assert_restored(backup.load(p))

    def test_plain_dump_is_still_byte_stable(self):
        """内部自动快照（pending / 升级前备份）用 dump()，可复现性不能被破坏。"""
        a, b = self.path("s1.json.gz"), self.path("s2.json.gz")
        backup.dump(a, self.payload)
        backup.dump(b, self.payload)
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_v1_legacy_backup_still_loads(self):
        p = self.path("v1.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"_version": 1, "folders": [], "notes": []}, f)
        d, _warn = backup.decode(backup.load(p))
        self.assertEqual(d["notes"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
