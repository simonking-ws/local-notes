# -*- coding: utf-8 -*-
"""secret（笔记加密）的测试。

加密代码最怕「看上去能跑、其实每次都一样」：密钥流固定、salt 复用、认证漏掉
某个头部字段，这些都不会让程序报错，只会让加密形同虚设。所以这里断言的重点
不是"解出来一样"，而是那些**必须不一样/必须报错**的地方。

运行：
    python tests\\test_secret.py
"""
import os
import sys
import json
import base64
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import secret                                                  # noqa: E402


def seal(plain, pw):
    """新建密文（只要 blob，不要密钥）。

    `secret.seal()` 原本是生产模块里的一个便捷包装，只被测试用 → 清理时删掉了，
    等价写法搬到这里，测试才不用每处都写 `seal_and_key(...)[0]`。
    """
    return secret.seal_and_key(plain, pw)[0]


PLAIN = json.dumps([["t", "机密：季度预算 1,200 万", ["bold"]],
                    ["t", "\n", []]], ensure_ascii=False)


class RoundtripTest(unittest.TestCase):

    def test_roundtrip(self):
        blob = seal(PLAIN, "hunter2")
        plain, key = secret.open_sealed(blob, "hunter2")
        self.assertEqual(plain, PLAIN)
        self.assertEqual(len(key), 32)

    def test_empty_text(self):
        blob = seal("", "1234")
        self.assertEqual(secret.open_sealed(blob, "1234")[0], "")

    def test_unicode_password(self):
        blob = seal(PLAIN, "口令也要能用中文")
        self.assertEqual(secret.open_sealed(blob, "口令也要能用中文")[0], PLAIN)

    def test_password_with_spaces_is_not_trimmed(self):
        # 口令里的空格是口令的一部分：悄悄 strip 掉，用户下次就解不开了
        blob = seal(PLAIN, " a b ")
        self.assertEqual(secret.open_sealed(blob, " a b ")[0], PLAIN)
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(blob, "a b")


class FailureTest(unittest.TestCase):

    def test_wrong_password(self):
        blob = seal(PLAIN, "right")
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(blob, "wrong")

    def test_tampered_ciphertext(self):
        #{先破坏密文，必须报错而不是解出乱码}
        blob = seal(PLAIN, "pw1234")
        raw = bytearray(base64.b64decode(blob["data"]))
        raw[0] ^= 0x01
        blob["data"] = base64.b64encode(bytes(raw)).decode("ascii")
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(blob, "pw1234")

    def test_tampered_header(self):
        # 改迭代数也是篡改：认证标签覆盖了全部头部字段
        blob = seal(PLAIN, "pw1234")
        blob["iter"] = int(blob["iter"]) + 1
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(blob, "pw1234")

    def test_broken_blob(self):
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed({"data": "xx"}, "pw1234")


class FreshnessTest(unittest.TestCase):

    def test_nonce_differs(self):
        """同一段明文、同一个口令，两次加密的密文必须不同（否则能看出内容相同）。"""
        a = seal(PLAIN, "pw1234")
        b = seal(PLAIN, "pw1234")
        self.assertNotEqual(a["data"], b["data"])
        self.assertNotEqual(a["nonce"], b["nonce"])

    def test_salt_differs(self):
        a = seal(PLAIN, "pw1234")
        b = seal(PLAIN, "pw1234")
        self.assertNotEqual(a["salt"], b["salt"])

    def test_reseal_keeps_salt(self):
        """用缓存密钥重新加密时必须沿用 salt：换 salt 等于换密钥，旧口令就再也解不开了。"""
        blob, key = secret.seal_and_key(PLAIN, "pw1234")
        again = secret.reseal_with_key(PLAIN + " 追加", blob, key)
        self.assertEqual(again["salt"], blob["salt"])
        self.assertEqual(again["iter"], blob["iter"])
        self.assertNotEqual(again["nonce"], blob["nonce"])
        # 关键：重新加密后，用口令仍然解得开最新那版
        plain, _k = secret.open_sealed(again, "pw1234")
        self.assertEqual(plain, PLAIN + " 追加")

    def test_reseal_invalidates_old(self):
        """换口令 = 用新口令重新 seal：新密文只认新口令，旧密文仍归旧口令。"""
        blob = seal(PLAIN, "old-pw")
        new = seal(PLAIN, "new-pw")
        self.assertEqual(secret.open_sealed(new, "new-pw")[0], PLAIN)
        with self.assertRaises(secret.BadPassword):
            secret.open_sealed(new, "old-pw")
        # 旧密文当然还能用旧口令打开（重新加密只影响新密文）
        self.assertEqual(secret.open_sealed(blob, "old-pw")[0], PLAIN)


class ShapeTest(unittest.TestCase):

    def test_is_blob(self):
        blob = seal(PLAIN, "pw1234")
        self.assertTrue(secret.is_blob(blob))
        self.assertFalse(secret.is_blob(None))
        self.assertFalse(secret.is_blob({}))
        self.assertFalse(secret.is_blob({"data": "x"}))     # 缺 salt

    def test_plaintext_not_in_blob(self):
        """密文里不能残留明文（哪怕是以 base64 之外的形式）。"""
        blob = seal(PLAIN, "pw1234")
        dump = json.dumps(blob, ensure_ascii=False)
        self.assertNotIn("季度预算", dump)


class HintTest(unittest.TestCase):
    """标题首字（hint）：锁着的笔记在列表里显示「首字 + 星号」用的那一个字。

    它是全库唯一刻意留下的明文痕迹，所以这里的断言既要看它能用，也要看它
    *不会被当成正经加密数据* —— 改它不该让认证失效，也不该影响解密。
    """

    def test_no_hint_until_stamped(self):
        blob = seal(PLAIN, "pw1234")
        self.assertEqual(secret.hint_of(blob), "")

    def test_roundtrip_and_only_one_char(self):
        blob = seal(PLAIN, "pw1234")
        secret.stamp_hint(blob, "机密：季度预算")
        self.assertEqual(secret.hint_of(blob), "机")
        self.assertEqual(secret.open_sealed(blob, "pw1234")[0], PLAIN)

    def test_hint_is_outside_the_auth_tag(self):
        """hint 不参与认证：改动它顶多让列表里的遮蔽标题显示错字。"""
        blob = seal(PLAIN, "pw1234")
        secret.stamp_hint(blob, "机")
        blob["hint"] = "假"
        self.assertEqual(secret.hint_of(blob), "假")
        # 仍然解得开 —— 它不在 mac 覆盖范围内，伪造不了明文，也挡不住正主
        self.assertEqual(secret.open_sealed(blob, "pw1234")[0], PLAIN)

    def test_hint_does_not_leak_the_rest_of_the_title(self):
        blob = seal(PLAIN, "pw1234")
        secret.stamp_hint(blob, "机")
        self.assertEqual(secret.hint_of(blob), "机")
        self.assertNotIn("季度预算", json.dumps(blob, ensure_ascii=False))

    def test_hint_of_tolerates_junk(self):
        for junk in (None, "", 0, {"data": "x"}):
            self.assertEqual(secret.hint_of(junk), "")
        # 只带 salt/data、没有 hint 字段的旧密文
        self.assertEqual(secret.hint_of({"salt": "s", "data": "d"}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
