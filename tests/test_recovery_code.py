# -*- coding: utf-8 -*-
"""每篇独立恢复码：纯函数层 + 落库 / 备份判据 + 业务流程（不建 GUI）。

锁住 2026-11-09 定下的语义：
  1. 恢复码**每篇一份**，程序现场生成、只展示一次、绝不落盘；
  2. 槽只是「同一把主密钥的第二份封装」，正文密文 / nonce / 主 tag 一个字节不动，
     旧版本程序读新 blob 照常能按口令解开；
  3. 取回 key 后仍要过主 tag，且**必须当场重设新口令**，落盘仍是密文；
  4. 只有 `Store._sig_of()` 感知槽，`backup.rev_of()` 故意不感知（合并去重语义）。

运行：
    python tests\\test_recovery_code.py
"""
import os
import sys
import json
import copy
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import secret                                                   # noqa: E402
import store                                                    # noqa: E402
import backup                                                   # noqa: E402
import app_actions                                              # noqa: E402
from app_actions import ActionsMixin                            # noqa: E402


def without_disk_write(fn, *args):
    """add_recovery_code 直接调模块级 save_data，测试里临时挡掉，别污染真实库。"""
    old = app_actions.save_data
    app_actions.save_data = lambda d: None
    try:
        return fn(*args)
    finally:
        app_actions.save_data = old


PLAIN = json.dumps([["t", "机密：季度预算 1,200 万", ["bold"]],
                    ["t", "\n", []]], ensure_ascii=False)
PW = "hunter2"
NEW_PW = "new-pass-2026"


def sealed(pw=PW):
    return secret.seal_and_key(PLAIN, pw)


def note_with_slot():
    """返回 (笔记, 恢复码显示串)。笔记是**未解锁**落库形态：content 为空。"""
    blob, key = sealed()
    code = secret.new_recovery_code()
    secret.seal_recovery(blob, key, code)
    secret.stamp_hint(blob, "机")
    n = {"id": "n_recovery", "folder": "f_work", "content": [],
         "sec": blob, "created": 1, "updated": 2, "color": "#2f9e57"}
    return n, code


class CodeFormatTest(unittest.TestCase):

    def test_24_chars_in_four_groups(self):
        code = secret.new_recovery_code()
        self.assertEqual([len(g) for g in code.split("-")], [6, 6, 6, 6])
        self.assertEqual(len(secret.norm_recovery_code(code)), 24)

    def test_codes_are_unique(self):
        self.assertEqual(len({secret.new_recovery_code() for _ in range(60)}), 60)

    def test_alphabet_has_no_confusables(self):
        self.assertEqual(len(secret.RC_ALPHABET), 32)
        for ch in "ILOU":
            self.assertNotIn(ch, secret.RC_ALPHABET)

    def test_norm_accepts_hyphens_spaces_lowercase(self):
        code = secret.new_recovery_code()
        messy = code.lower().replace("-", " ")
        self.assertEqual(secret.norm_recovery_code(messy),
                         secret.norm_recovery_code(code))

    def test_norm_maps_handwritten_confusables(self):
        code = secret.new_recovery_code()
        c = secret.norm_recovery_code(code)
        swapped = c.replace("1", "I").replace("0", "O")
        self.assertEqual(secret.norm_recovery_code(swapped), c)

    def test_norm_rejects_bad_length_or_char(self):
        self.assertIsNone(secret.norm_recovery_code("ABC"))
        self.assertIsNone(secret.norm_recovery_code("!" * 24))
        self.assertIsNone(secret.norm_recovery_code(None))
        self.assertIsNone(secret.norm_recovery_code(12345))

    def test_recovery_id_is_public_and_stable(self):
        a = secret.new_recovery_code()
        b = secret.new_recovery_code()
        rid = secret.recovery_id(a)
        self.assertEqual(len(rid), 8)
        self.assertEqual(rid, secret.recovery_id(
            secret.norm_recovery_code(a)))
        self.assertNotEqual(rid, secret.recovery_id(b))
        self.assertNotEqual(rid, secret.norm_recovery_code(a))
        self.assertIn(" ", secret.recovery_id_display(rid))


class RecordFormatTest(unittest.TestCase):
    """「复制恢复码」给的是整条凭证：三段【】用下划线连，且能原样粘回输入框。"""

    def _sample(self, title="季度预算"):
        code = secret.new_recovery_code()
        rid = secret.recovery_id(code)
        return title, rid, code, secret.format_recovery_record(title, rid, code)

    def test_record_is_three_bracketed_fields(self):
        title, rid, code, record = self._sample()
        self.assertEqual(
            record,
            "[%s]-[%s]-[%s]" % (title, secret.recovery_id_display(rid), code))
        self.assertEqual(record.count("["), 3)
        self.assertEqual(record.count("]"), 3)

    def test_title_with_separator_or_brackets_still_parses(self):
        _, _, code, record = self._sample("2026_季度[预算]  第三\n版")
        self.assertEqual(record.count("["), 3,
                         "笔记名称里的方括号 / 下划线会把三段撑成四段")
        self.assertNotIn("[预算]", record)
        self.assertEqual(secret.parse_recovery_record(record), code)

    def test_parse_plain_code_is_untouched(self):
        code = secret.new_recovery_code()
        self.assertEqual(secret.parse_recovery_record(code), code)
        self.assertIsNone(secret.parse_recovery_record(None))
        self.assertEqual(secret.parse_recovery_record(12345), 12345)

    def test_only_parsed_record_passes_norm(self):
        _, _, code, record = self._sample()
        self.assertIsNone(secret.norm_recovery_code(record),
                          "整条凭证不该被误当成一段纯码")
        self.assertEqual(
            secret.norm_recovery_code(secret.parse_recovery_record(record)),
            secret.norm_recovery_code(code))

    def test_bad_record_falls_through_to_norm(self):
        bad = "[乱码]-[x]-[BAD]"
        self.assertEqual(secret.parse_recovery_record(bad), bad)
        self.assertIsNone(secret.norm_recovery_code(bad))

    def test_copied_record_can_unlock_the_slot(self):
        """复制出去再粘回来的那一串，必须还能真的解开恢复槽。"""
        n, code = note_with_slot()
        rid = secret.recovery_of(n["sec"])["rid"]
        record = secret.format_recovery_record("季度预算", rid, code)
        got = secret.norm_recovery_code(secret.parse_recovery_record(record))
        key = secret.open_recovery(n["sec"], got)
        self.assertEqual(secret.open_with_key(n["sec"], key), PLAIN)


class SlotTest(unittest.TestCase):

    def test_roundtrip_and_old_password_path_both_work(self):
        n, code = note_with_slot()
        blob = n["sec"]
        key = secret.open_recovery(blob, code)
        self.assertEqual(secret.open_with_key(blob, key), PLAIN)
        self.assertEqual(secret.open_sealed(blob, PW)[0], PLAIN,
                         "挂了槽之后口令路径必须照常可用（旧版程序兼容）")

    def test_wrong_code_is_rejected_before_kdf(self):
        n, _ = note_with_slot()
        with self.assertRaises(secret.BadPassword) as cm:
            secret.open_recovery(n["sec"], secret.new_recovery_code())
        self.assertIn("不属于", str(cm.exception))

    def test_tampered_wrap_is_rejected(self):
        n, code = note_with_slot()
        blob = copy.deepcopy(n["sec"])
        blob["recovery"]["wrap"] = secret._b64e(b"\x00" * 32)
        with self.assertRaises(secret.BadPassword):
            secret.open_recovery(blob, code)

    def test_tampered_salt_and_iter_are_rejected(self):
        n, code = note_with_slot()
        for key, val in (("salt", secret._b64e(b"\x01" * 16)),
                         ("iter", 1), ("rid", "DEADBEEF")):
            blob = copy.deepcopy(n["sec"])
            blob["recovery"][key] = val
            with self.assertRaises(secret.BadPassword):
                secret.open_recovery(blob, code)

    def test_slot_cannot_be_moved_to_another_note(self):
        n, code = note_with_slot()
        other, _ = sealed("another-password")
        other["recovery"] = copy.deepcopy(n["sec"]["recovery"])
        with self.assertRaises(secret.BadPassword):
            secret.open_recovery(other, code)

    def test_each_seal_uses_a_fresh_salt(self):
        blob, key = sealed()
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        first = dict(blob["recovery"])
        secret.seal_recovery(blob, key, code)
        second = blob["recovery"]
        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["wrap"], second["wrap"])

    def test_v1_blob_without_slot(self):
        blob, _key = sealed()
        self.assertFalse(secret.has_recovery(blob))
        with self.assertRaises(secret.BadPassword) as cm:
            secret.open_recovery(blob, secret.new_recovery_code())
        self.assertIn("没有设置", str(cm.exception))
        self.assertEqual(secret.open_sealed(blob, PW)[0], PLAIN)

    def test_drop_recovery(self):
        n, code = note_with_slot()
        secret.drop_recovery(n["sec"])
        self.assertFalse(secret.has_recovery(n["sec"]))
        self.assertEqual(secret.open_sealed(n["sec"], PW)[0], PLAIN)

    def test_blob_never_contains_the_code_or_plaintext(self):
        n, code = note_with_slot()
        raw = json.dumps(n["sec"], ensure_ascii=False)
        self.assertNotIn(secret.norm_recovery_code(code), raw)
        self.assertNotIn(code, raw)
        self.assertNotIn("季度预算", raw)
        self.assertEqual(secret.recovery_of(n["sec"])["rid"],
                         secret.recovery_id(code))

    def test_hint_is_still_outside_the_main_tag(self):
        blob, key = sealed()
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        tag = blob["tag"]
        blob["hint"] = "改"
        self.assertEqual(blob["tag"], tag)
        self.assertEqual(secret.open_with_key(blob, key), PLAIN)


class ResealTest(unittest.TestCase):
    """2026-11-09 返修：编辑 / 切走笔记会 reseal，不能把恢复槽弄丢或弄失效。

    旧实现有两处叠加的错：reseal_with_key 构造全新 blob 不带 recovery；且槽的
    AAD 绑了每次重加密都变的主 tag/data，就算把槽复制回来也验不过。结果是设完
    恢复码一敲字，has_recovery() 变 False，两个「用恢复码恢复」入口一起消失。
    """

    def test_repeated_reseal_keeps_slot_and_code(self):
        blob, key = secret.seal_and_key(PLAIN, PW)
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        for i in range(3):
            plain = json.dumps(json.loads(PLAIN) + [["t", "第%d次编辑" % i, []]],
                               ensure_ascii=False)
            blob = secret.reseal_with_key(plain, blob, key)
            self.assertTrue(secret.has_recovery(blob),
                            "第%d次重加密把槽弄丢了" % (i + 1))
            self.assertEqual(secret.open_recovery(blob, code), key,
                             "第%d次重加密后旧码失效" % (i + 1))
            self.assertEqual(secret.open_sealed(blob, PW)[0], plain)

    def test_reseal_changes_ciphertext_but_not_slot(self):
        blob, key = secret.seal_and_key(PLAIN, PW)
        secret.seal_recovery(blob, key, secret.new_recovery_code())
        rc_before = dict(secret.recovery_of(blob))
        before = (blob["nonce"], blob["data"], blob["tag"])
        blob = secret.reseal_with_key(PLAIN, blob, key)
        self.assertNotEqual((blob["nonce"], blob["data"], blob["tag"]), before)
        self.assertEqual(secret.recovery_of(blob), rc_before,
                         "槽跟的是那把 key，不该随正文密文重算")

    def test_aad_binds_main_salt(self):
        blob, key = secret.seal_and_key(PLAIN, PW)
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        tam = copy.deepcopy(blob)
        tam["salt"] = secret._b64e(b"\x07" * 16)
        with self.assertRaises(secret.BadPassword):
            secret.open_recovery(tam, code)

    def test_legacy_v1_slot_is_still_readable(self):
        import hashlib
        import hmac
        blob, key = secret.seal_and_key(PLAIN, PW)
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        rc = secret.recovery_of(blob)
        rc["v"] = 1
        kek = hashlib.pbkdf2_hmac(
            "sha256", secret.norm_recovery_code(code).encode("ascii"),
            secret._b64d(rc["salt"]), rc["iter"], 32)
        rc["tag"] = secret._b64e(hmac.new(
            secret._subkeys(kek)[1], secret._rc_aad_legacy(blob, rc),
            hashlib.sha256).digest())
        self.assertEqual(secret.open_recovery(blob, code), key,
                         "老库里的 v1 槽不能被这次改动判死")


class PersistenceTest(unittest.TestCase):

    def _sig(self, n):
        return store.Store._sig_of(None, n)     # 只看 n，不碰 DB

    def test_sig_detects_slot_add_and_removal(self):
        blob, key = sealed()
        n = {"content": [], "sec": blob}
        before = self._sig(n)
        code = secret.new_recovery_code()
        secret.seal_recovery(blob, key, code)
        after = self._sig(n)
        self.assertNotEqual(before, after, "只加槽也必须落盘")
        secret.drop_recovery(blob)
        self.assertEqual(self._sig(n), before)

    def test_rev_of_ignores_slot(self):
        """合并去重只认正文：补个槽不该让同一份备份导两次变成两篇。"""
        blob, key = sealed()
        n = {"content": [], "sec": blob}
        before = backup.rev_of(n)
        secret.seal_recovery(blob, key, secret.new_recovery_code())
        self.assertEqual(backup.rev_of(n), before)

    def test_backup_note_roundtrip_keeps_the_slot(self):
        n, code = note_with_slot()
        enc = backup._encode_note(n)
        self.assertTrue(secret.has_recovery(enc["sec"]))
        out = backup._decode_note(enc)
        self.assertTrue(secret.has_recovery(out["sec"]))
        self.assertEqual(secret.open_recovery(out["sec"], code),
                         secret.open_recovery(n["sec"], code))

    def test_full_backup_roundtrip_keeps_the_slot(self):
        n, code = note_with_slot()
        data = {"folders": [], "notes": [n], "settings": {}, "styles": {}}
        payload, _warn = backup.decode(backup.encode(data))
        got = next(x for x in payload["notes"] if x["id"] == n["id"])
        self.assertTrue(secret.has_recovery(got["sec"]))
        self.assertEqual(secret.open_with_key(
            got["sec"], secret.open_recovery(got["sec"], code)), PLAIN)

    def test_code_is_not_in_prefs_or_state(self):
        self.assertNotIn("recovery_code", backup.PREFS_KEYS)
        self.assertNotIn("recovery_code", backup.STATE_KEYS)
        self.assertNotIn("recovery", backup.STATE_KEYS)


class _FlowStub(object):
    """只实现恢复 / 设置流程会碰到的那几个钩子，不建 Tk、不写真实库。"""

    def __init__(self, n):
        self.data = {"notes": [n], "folders": [],
                     "settings": {}, "styles": {}, "current_note": n["id"]}
        self._keys = {}
        self._cur_pw = None
        self.infos = []
        self.errors = []
        self.input_code = None
        self.new_pw = NEW_PW
        self.new_code = secret.new_recovery_code()

    def _pick_note(self, nid=None):
        return self.data["notes"][0]

    def is_protected_note(self, n):
        return False

    def _sync_editor_to(self, n):
        pass

    def _head_char(self, n):
        return "机"

    def _show_info(self, title, message):
        self.infos.append((title, message))

    def _show_error(self, title, message):
        self.errors.append((title, message))

    def _ask_recovery_code(self, title, name, rid=""):
        return self.input_code

    def _ask_pw_twice(self, title, head):
        return self.new_pw

    def _offer_recovery_code(self, n, blob, key, replacing=False):
        secret.seal_recovery(blob, key, self.new_code)
        return secret.norm_recovery_code(self.new_code)

    def _apply_new_seal(self, n, blob, key, pw, content=None):
        n["sec"] = blob
        if content is not None:
            n["content"] = content
        self._keys[n["id"]] = key
        self._cur_pw = pw

    def refresh_notes(self):
        pass

    def _refresh_folder_counts(self):
        pass

    def load_editor(self):
        pass

    def _ask_confirm(self, *a, **kw):
        return True

    def _ensure_unlocked(self, n, force=False):
        return True

    def _note_key(self, nid):
        return self._keys.get(nid)


class FlowTest(unittest.TestCase):

    def test_recover_rekeys_and_invalidates_old_code(self):
        n, old_code = note_with_slot()
        stub = _FlowStub(n)
        stub.input_code = old_code
        old_blob = copy.deepcopy(n["sec"])
        self.assertTrue(ActionsMixin.recover_note_with_code(stub, n))
        self.assertEqual(n["content"], json.loads(PLAIN))
        self.assertEqual(secret.open_sealed(n["sec"], NEW_PW)[0], PLAIN,
                         "恢复后落盘仍是密文，用新口令打开")
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(n["sec"], PW)
        with self.assertRaises(secret.BadPassword):
            secret.open_recovery(n["sec"], old_code)
        self.assertEqual(secret.open_with_key(
            n["sec"], secret.open_recovery(n["sec"], stub.new_code)), PLAIN)
        self.assertEqual(stub._cur_pw, NEW_PW)
        self.assertNotEqual(n["sec"]["tag"], old_blob["tag"])

    def test_wrong_code_leaves_blob_untouched(self):
        n, _ = note_with_slot()
        stub = _FlowStub(n)
        stub.input_code = secret.new_recovery_code()
        before = copy.deepcopy(n["sec"])
        self.assertFalse(ActionsMixin.recover_note_with_code(stub, n))
        self.assertEqual(n["sec"], before)
        self.assertTrue(stub.errors)

    def test_cancel_at_new_password_leaves_blob_untouched(self):
        n, code = note_with_slot()
        stub = _FlowStub(n)
        stub.input_code = code
        stub.new_pw = None
        before = copy.deepcopy(n["sec"])
        self.assertFalse(ActionsMixin.recover_note_with_code(stub, n))
        self.assertEqual(n["sec"], before)
        self.assertEqual(n["content"], [])

    def test_no_slot_refuses_to_recover(self):
        blob, _key = sealed()
        n = {"id": "n_v1", "folder": "f_work", "content": [], "sec": blob,
             "created": 1, "updated": 2}
        stub = _FlowStub(n)
        self.assertFalse(ActionsMixin.recover_note_with_code(stub, n))
        self.assertIn("没有设置恢复码", stub.infos[0][1])

    def test_set_password_attaches_a_slot(self):
        n = {"id": "n_plain", "folder": "f_work",
             "content": json.loads(PLAIN), "created": 1, "updated": 2}
        stub = _FlowStub(n)
        old_pw = stub.new_pw
        stub.new_pw = PW
        ActionsMixin.set_note_password(stub, n["id"])
        self.assertTrue(secret.has_recovery(n["sec"]))
        self.assertEqual(secret.open_sealed(n["sec"], PW)[0], PLAIN)
        self.assertEqual(stub._cur_pw, PW)
        stub.new_pw = old_pw

    def test_change_password_replaces_the_slot(self):
        n, old_code = note_with_slot()
        stub = _FlowStub(n)
        n["content"] = json.loads(PLAIN)
        stub._keys[n["id"]] = secret.open_sealed(n["sec"], PW)[1]
        stub._cur_pw = PW
        stub.new_pw = NEW_PW
        ActionsMixin.change_note_password(stub, n["id"])
        self.assertEqual(secret.open_sealed(n["sec"], NEW_PW)[0], PLAIN)
        self.assertTrue(secret.has_recovery(n["sec"]))
        with self.assertRaises(secret.BadPassword):
            secret.open_recovery(n["sec"], old_code)
        self.assertEqual(secret.open_recovery(n["sec"], stub.new_code),
                         secret.open_sealed(n["sec"], NEW_PW)[1],
                         "改口令后新槽里封的必须是新口令派生出的那把 key")

    def test_add_recovery_code_upgrades_v1_note(self):
        blob, key = sealed()
        n = {"id": "n_v1", "folder": "f_work",
             "content": json.loads(PLAIN), "sec": blob,
             "created": 1, "updated": 2}
        stub = _FlowStub(n)
        stub._keys[n["id"]] = key
        without_disk_write(ActionsMixin.add_recovery_code, stub, n["id"])
        self.assertTrue(secret.has_recovery(n["sec"]))
        self.assertEqual(secret.open_recovery(n["sec"], stub.new_code), key)
        self.assertEqual(secret.open_sealed(n["sec"], PW)[0], PLAIN)

    def test_seal_into_after_editing_keeps_the_slot(self):
        """走真实业务链路：改正文 → _seal_into → 槽仍在、用户手上的码仍有效。"""
        n, code = note_with_slot()
        stub = _FlowStub(n)
        stub._keys[n["id"]] = secret.open_sealed(n["sec"], PW)[1]
        n["content"] = json.loads(PLAIN) + [["t", "刚敲的一行", []]]
        self.assertTrue(ActionsMixin._seal_into(stub, n))
        self.assertTrue(secret.has_recovery(n["sec"]))
        self.assertEqual(secret.open_recovery(n["sec"], code),
                         secret.open_sealed(n["sec"], PW)[1])
        self.assertIn("刚敲的一行",
                      secret.open_sealed(n["sec"], PW)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
