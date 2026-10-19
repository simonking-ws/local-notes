# -*- coding: utf-8 -*-
"""素笺 —— 笔记加密（纯标准库，不 import tkinter）。

为什么不引第三方库：本项目的卖点是「零第三方依赖 + 单目录 + python scripts/notes_app.py
直接跑」，给笔记加个口令不值得为此破例。拆成独立模块是为了它能在 tests/ 里不建 GUI 直接断言
——加密代码最怕「看上去能跑、其实每次都用同一段密钥流」。

方案（口令 → 密文）：
    salt     每次新建口令时随机 16 字节，之后**固定不变**（换 salt 等于换密钥，
             已存的密文就再也解不开了，所以重新加密时必须复用同一组 salt/iter）
    key      PBKDF2-HMAC-SHA256(口令, salt, iter=20 万) → 32 字节
    enc_key  HMAC-SHA256(key, b"enc")     密钥流用
    mac_key  HMAC-SHA256(key, b"mac")     认证用
    密文     = 明文 XOR SHA256(enc_key || nonce || 计数器)，即 SHA256 当伪随机函数的 CTR 模式
    认证     = HMAC-SHA256(mac_key, v || iter || salt || nonce || 密文)

先验认证再解密：口令错、密文被改动，都会在解密之前就报 BadPassword，
不会把一段乱码当成正文渲染出来。

这当然不是"军用级"加密，但它是**正确拼装**的对称加密：随机 nonce、先认证后解密、
口令与派生密钥都不落盘、篡改可发现。威胁模型是"捡到 notes_data.db 的人"，
不是能离线跑几亿次 PBKDF2 的国家级攻击者。
"""
import os
import hmac
import base64
import hashlib
import struct

VERSION = 1
ITERATIONS = 200000          # 20 万次：本机约 60~80ms，解锁时一次性付出，可接受
SALT_LEN = 16
NONCE_LEN = 12
_KEY_LEN = 32


class BadPassword(Exception):
    """口令错误，或密文被改动 / 截断（两者对外无法区分，也不该区分）。"""


def _b64e(b):
    return base64.b64encode(b).decode("ascii")


def _b64d(s):
    return base64.b64decode((s or "").encode("ascii"))


def _subkeys(key):
    enc = hmac.new(key, b"enc", hashlib.sha256).digest()
    mac = hmac.new(key, b"mac", hashlib.sha256).digest()
    return enc, mac


def _keystream(enc_key, nonce, n):
    """生成 n 字节密钥流：每块 = SHA256(enc_key || nonce || 计数器)。"""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(enc_key + nonce + struct.pack(">Q", counter)).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


def _auth(mac_key, blob):
    """认证标签覆盖全部头部字段：改版本号 / 迭代数 / salt / nonce 都会被识破。"""
    return hmac.new(mac_key, b"|".join([
        b"v1",
        str(blob["iter"]).encode("ascii"),
        _b64d(blob["salt"]),
        _b64d(blob["nonce"]),
        _b64d(blob["data"]),
    ]), hashlib.sha256).digest()


def derive_key(password, salt, iterations=ITERATIONS):
    """口令 + salt → 32 字节主密钥。"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, int(iterations), _KEY_LEN)


def seal(plain_text, password):
    """新建密文：随机 salt + 随机 nonce，返回可直接 json 落库的 dict。"""
    return seal_and_key(plain_text, password)[0]


def seal_and_key(plain_text, password):
    """同上，但同时把派生出的密钥交回去 —— 解锁之后的每次编辑都要复用它，
    不能再跑一遍 20 万次 PBKDF2。"""
    salt = os.urandom(SALT_LEN)
    key = derive_key(password, salt, ITERATIONS)
    blob = reseal_with_key(plain_text, {"salt": _b64e(salt), "iter": ITERATIONS}, key)
    return blob, key


def reseal_with_key(plain_text, old_blob, key):
    """用**已缓存的密钥**重新加密（编辑时每敲几个字就要做一次，不能再跑 PBKDF2）。

    salt / iter 必须沿用旧 blob：密钥是从「口令 + 那个 salt」推出来的，
    换一组 salt 只是让以后用口令再也解不开（密钥没变，密文仍然对）。
    """
    salt_b64 = old_blob["salt"]
    iters = int(old_blob.get("iter") or ITERATIONS)
    nonce = os.urandom(NONCE_LEN)
    enc_key, mac_key = _subkeys(key)
    data = plain_text.encode("utf-8")
    ct = _xor(data, _keystream(enc_key, nonce, len(data)))
    blob = {"v": VERSION, "iter": iters, "salt": salt_b64,
            "nonce": _b64e(nonce), "data": _b64e(ct), "tag": ""}
    blob["tag"] = _b64e(_auth(mac_key, blob))
    return blob


def open_sealed(blob, password):
    """校验并解密，返回 (明文, 密钥)。口令错 / 密文被改动都抛 BadPassword。"""
    try:
        iters = int(blob.get("iter") or ITERATIONS)
        salt = _b64d(blob["salt"])
    except Exception:
        raise BadPassword("密文头损坏")
    key = derive_key(password, salt, iters)
    enc_key, mac_key = _subkeys(key)
    try:
        want = _b64d(blob.get("tag"))
        got = _auth(mac_key, blob)
    except Exception:
        raise BadPassword("密文头损坏")
    if not hmac.compare_digest(want, got):
        raise BadPassword("口令不正确")
    try:
        ct = _b64d(blob["data"])
        nonce = _b64d(blob["nonce"])
        return _xor(ct, _keystream(enc_key, nonce, len(ct))).decode("utf-8"), key
    except BadPassword:
        raise
    except Exception:
        raise BadPassword("密文损坏")


def is_blob(x):
    """判断某个字段像不像本模块产出的密文。"""
    return isinstance(x, dict) and bool(x.get("data")) and bool(x.get("salt"))


# 密文块里「标题首字」的字段名。锁着的笔记在列表里要显示「首字 + 星号」，
# 而那时正文一个字符都读不出来 —— 只能把这个字存在密文块里跟着密文走
# （换密文它跟着换、取消加密它跟着消失、导出备份也带着）。
# 这是全库唯一「刻意留下的明文痕迹」，泄漏量固定为一个汉字。
# 注意它不参与 _auth 认证：改它顶多让遮蔽标题显示错字，没有伪造明文的可能。
HINT_KEY = "hint"


def stamp_hint(blob, head):
    """把标题首字写进密文块（head 取正文第一行的第一个字符，可为空）。"""
    if isinstance(blob, dict):
        blob[HINT_KEY] = (head or "")[:1]
    return blob


def hint_of(blob):
    """读出密文块里存的标题首字（没有则空串，如升级前就加密的老笔记）。"""
    return (blob.get(HINT_KEY) or "") if isinstance(blob, dict) else ""
