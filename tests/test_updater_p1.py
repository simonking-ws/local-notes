# -*- coding: utf-8 -*-
"""自动更新（下载 / 校验 / 解包 / 替换脚本）的单测。

网络部分全部打本机回环上的 HTTP 服务（``127.0.0.1:0`` 随机端口），不碰 GitHub：
这些测试要能在地铁上跑，而且真正想验证的是**我们自己的逻辑**，不是 GitHub 通不通。

这里每条用例都对应一个真实会出事的地方，写的时候不是凑数量：

- 续传后哈希必须重算（否则"断过线的用户"永远校验失败，而正常用户不会）
- 服务端不理 Range 时不能把新内容接在旧内容后面
- zip-slip / 包里夹带 data/ 必须在落地前拦住（拦住的位置错了就是笔记被覆盖）
- 批处理必须全 ASCII，且必须带 ``/XD data``（漏了这条，用户笔记跟着旧版本一起没）

    python tests\\test_updater_p1.py
或：
    python -m unittest discover -s tests -v
"""
import hashlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import updater


class _Handler(http.server.BaseHTTPRequestHandler):
    """支持 Range 的最小文件服务。``server.files`` 是 {path: bytes}。"""

    # HTTP/1.0：不要 keep-alive，否则测试结束时一堆 socket 会报 ResourceWarning
    protocol_version = "HTTP/1.0"

    def do_GET(self):                       # noqa: N802
        files = self.server.files
        path = self.path.split("?")[0]
        if path not in files:
            self.send_error(404)
            return
        data = files[path]
        rng = self.headers.get("Range") or ""
        start = 0
        code = 200
        if rng.startswith("bytes=") and self.server.honor_range:
            start = int(rng[6:].split("-")[0] or 0)
            code = 206
        body = data[start:]
        self.send_response(code)
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d"
                             % (start, len(data) - 1, len(data)))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):              # 测试输出保持干净
        pass


class _Server(object):
    def __init__(self, files, honor_range=True):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.files = files
        self.httpd.honor_range = honor_range
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.httpd.server_address[1], path)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _make_zip(path, entries):
    """entries = {"素笺/素笺.exe": b"...", ...}"""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


class _Tmp(unittest.TestCase):
    """把 LOCALAPPDATA 指向临时目录 —— update_root() 是每次读环境变量的，
    所以不用 mock 函数，测试之间天然隔离。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian_upd_")
        self._old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


# ------------------------------ 下载 ------------------------------

class TestDownload(_Tmp):
    def test_plain_download(self):
        data = b"hello sujian" * 5000
        with _Server({"/a.zip": data}) as srv:
            dest = os.path.join(self.tmp, "d", "a.zip")
            got = updater.download(srv.url("/a.zip"), dest)
        self.assertEqual(got, _sha(data))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertFalse(os.path.exists(dest + ".part"), "下完没把 .part 改名")

    def test_progress_reports_total(self):
        data = b"x" * 10000
        prog = {}
        with _Server({"/a.zip": data}) as srv:
            updater.download(srv.url("/a.zip"), os.path.join(self.tmp, "a.zip"),
                             total=len(data), progress=prog)
        self.assertEqual(prog["total"], len(data))
        self.assertEqual(prog["done"], len(data))

    def test_resume_from_part(self):
        """断点续传：已下好的那段必须也进哈希，否则校验必错。"""
        data = b"A" * 20000 + b"B" * 20000
        dest = os.path.join(self.tmp, "a.zip")
        with open(dest + ".part", "wb") as f:
            f.write(data[:25000])
        with _Server({"/a.zip": data}) as srv:
            got = updater.download(srv.url("/a.zip"), dest, total=len(data))
        self.assertEqual(got, _sha(data), "续传后哈希不对：.part 那段没喂给 hashlib")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_server_ignores_range(self):
        """服务端回 200 而不是 206：必须从头写，不能把新内容接在旧内容后面。"""
        data = b"C" * 40000
        dest = os.path.join(self.tmp, "a.zip")
        with open(dest + ".part", "wb") as f:
            f.write(b"Z" * 25000)           # 一份来路不明的"半个文件"
        with _Server({"/a.zip": data}, honor_range=False) as srv:
            got = updater.download(srv.url("/a.zip"), dest, total=len(data))
        self.assertEqual(got, _sha(data))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data, "拼出了「旧内容 + 新内容」的怪文件")

    def test_cancel_keeps_part(self):
        data = b"D" * 300000
        dest = os.path.join(self.tmp, "a.zip")

        class P(dict):
            def __setitem__(self, k, v):
                dict.__setitem__(self, k, v)
                if k == "done" and v > 100000:
                    dict.__setitem__(self, "cancel", True)

        with _Server({"/a.zip": data}) as srv:
            with self.assertRaises(updater.Cancelled):
                updater.download(srv.url("/a.zip"), dest, progress=P())
        self.assertFalse(os.path.exists(dest))
        self.assertTrue(os.path.exists(dest + ".part"), "取消后应保留断点")
        self.assertLess(os.path.getsize(dest + ".part"), len(data))

    def test_http_404_is_friendly(self):
        with _Server({}) as srv:
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.download(srv.url("/nope.zip"), os.path.join(self.tmp, "a.zip"))
        self.assertIn("HTTP 404", str(ctx.exception))

    def test_connection_refused(self):
        """连不上时不抛裸异常，而是给一句人话。

        不断言具体是哪句：本机若配了系统代理，请求会先到代理、由代理回 5xx，
        和"直连被拒"是不同的分支，但两者都该走 UpdateError。
        """
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.download("http://127.0.0.1:1/a.zip",
                             os.path.join(self.tmp, "a.zip"), timeout=3)
        self.assertTrue(str(ctx.exception).strip())


# ------------------------------ 解包 ------------------------------

class TestPackage(_Tmp):
    def _zip(self, entries):
        return _make_zip(os.path.join(self.tmp, "p.zip"), entries)

    def test_package_root_nested(self):
        zp = self._zip({"素笺/素笺.exe": b"exe", "素笺/_internal/x.dll": b"dll"})
        pkg = os.path.join(self.tmp, "pkg")
        with zipfile.ZipFile(zp) as zf:
            updater._safe_extract(zf, pkg)
        self.assertEqual(os.path.basename(updater.package_root(pkg)), "素笺")

    def test_package_root_flat(self):
        """作者可能把文件直接铺在 zip 根上 —— 也得认。"""
        zp = self._zip({"素笺.exe": b"exe", "_internal/x.dll": b"dll"})
        pkg = os.path.join(self.tmp, "pkg")
        with zipfile.ZipFile(zp) as zf:
            updater._safe_extract(zf, pkg)
        self.assertEqual(updater.package_root(pkg), os.path.abspath(pkg))

    def test_package_root_missing(self):
        zp = self._zip({"readme.txt": b"hi"})
        pkg = os.path.join(self.tmp, "pkg")
        with zipfile.ZipFile(zp) as zf:
            updater._safe_extract(zf, pkg)
        self.assertEqual(updater.package_root(pkg), "")

    def test_zip_slip_blocked(self):
        zp = self._zip({"../evil.exe": b"x"})
        with zipfile.ZipFile(zp) as zf:
            with self.assertRaises(updater.UpdateError):
                updater._safe_extract(zf, os.path.join(self.tmp, "pkg"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.exe")))

    def test_zip_slip_absolute(self):
        zp = self._zip({"/etc/passwd": b"x"})
        with zipfile.ZipFile(zp) as zf:
            with self.assertRaises(updater.UpdateError):
                updater._safe_extract(zf, os.path.join(self.tmp, "pkg"))


# ------------------------------ prepare ------------------------------

def _release(url, size, digest=""):
    return {"version": "v9.9.9", "html_url": "", "body": "", "assets": [
        {"name": "local-notes.zip", "size": size, "url": url, "digest": digest}]}


class TestPrepare(_Tmp):
    def setUp(self):
        super(TestPrepare, self).setUp()
        self.zip_path = _make_zip(os.path.join(self.tmp, "src.zip"), {
            "素笺/素笺.exe": b"new exe",
            "素笺/_internal/base.dll": b"new dll",
        })
        with open(self.zip_path, "rb") as f:
            self.blob = f.read()

    def test_happy_path_verified(self):
        with _Server({"/p.zip": self.blob}) as srv:
            res = updater.prepare(_release(srv.url("/p.zip"), len(self.blob),
                                          "sha256:" + _sha(self.blob)))
        self.assertTrue(res["verified"])
        self.assertEqual(res["sha256"], _sha(self.blob))
        self.assertEqual(os.path.basename(res["src"]), "素笺")
        self.assertTrue(os.path.isfile(res["exe"]))
        self.assertEqual(os.path.basename(res["dir"]), "v9.9.9")

    def test_no_digest_still_works(self):
        """老的 release 没有 digest 字段 —— 不能因此拒绝更新。"""
        with _Server({"/p.zip": self.blob}) as srv:
            res = updater.prepare(_release(srv.url("/p.zip"), len(self.blob)))
        self.assertIsNone(res["verified"])
        self.assertTrue(os.path.isfile(res["exe"]))

    def test_digest_mismatch_rejects_and_cleans(self):
        with _Server({"/p.zip": self.blob}) as srv:
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.prepare(_release(srv.url("/p.zip"), len(self.blob),
                                         "sha256:" + "0" * 64))
        self.assertIn("校验", str(ctx.exception))
        work = os.path.join(updater.update_root(), "v9.9.9")
        self.assertFalse(os.path.exists(os.path.join(work, "package.zip")),
                         "校验失败的包必须删掉，否则下次会拿它冒充")

    def test_data_dir_in_package_rejected(self):
        """包里夹带 data/ 就是笔记被覆盖的开始，必须落地前拦住。"""
        zp = _make_zip(os.path.join(self.tmp, "bad.zip"), {
            "素笺/素笺.exe": b"e", "素笺/data/notes_data.db": b"oops"})
        with open(zp, "rb") as f:
            blob = f.read()
        with _Server({"/p.zip": blob}) as srv:
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.prepare(_release(srv.url("/p.zip"), len(blob)))
        self.assertIn("data", str(ctx.exception))

    def test_missing_exe_rejected(self):
        zp = _make_zip(os.path.join(self.tmp, "bad.zip"), {"readme.txt": b"hi"})
        with open(zp, "rb") as f:
            blob = f.read()
        with _Server({"/p.zip": blob}) as srv:
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.prepare(_release(srv.url("/p.zip"), len(blob)))
        self.assertIn("素笺.exe", str(ctx.exception))

    def test_no_asset(self):
        with self.assertRaises(updater.UpdateError):
            updater.prepare({"version": "1.0", "assets": []})

    def test_reuses_verified_zip(self):
        """已经下好且校验过的包不该重复下载（用户重试 / 点了两次时省三分钟）。"""
        with _Server({"/p.zip": self.blob}) as srv:
            url = srv.url("/p.zip")
            rel = _release(url, len(self.blob), "sha256:" + _sha(self.blob))
            updater.prepare(rel)
            with mock.patch.object(updater, "download",
                                   side_effect=AssertionError("不该再下一遍")):
                res = updater.prepare(rel)
        self.assertTrue(res["verified"])


# ------------------------------ 替换脚本 ------------------------------

class TestApplyScript(_Tmp):
    def test_bat_is_ascii(self):
        work = os.path.join(self.tmp, "w")
        path = updater.write_apply_bat(work)
        with open(path, "rb") as f:
            raw = f.read()
        bad = [b for b in bytearray(raw) if b > 127]
        self.assertFalse(bad, "批处理含非 ASCII 字节：中文机器上会乱码")
        self.assertIn(b"\r\n", raw, "必须是 CRLF")

    def test_bat_protects_data(self):
        text = updater.BAT
        # 这三条对应三个真实事故：镜像删数据、timeout 在重定向时报错、自删失败
        self.assertIn('/XD "%SRC%\\data"', text)
        self.assertNotIn("/MIR", text, "用了 /MIR 会把用户放在安装目录的东西一起删掉")
        self.assertIn("ping -n 2 127.0.0.1", text)
        self.assertIn('(goto) 2>nul & del "%~f0"', text)
        self.assertIn("if errorlevel 8 goto fail", text)

    def test_xd_must_be_source_path(self):
        """这条是有来历的：/XD 若写成 %DST%\\data，robocopy 一声不响地忽略它
        —— 因为 /XD 的路径是按**源**树匹配的（目的路径只在 /MIR 清理时才有意义）。
        实测代价是：往包里塞一份假 data\\notes_data.db，用户的库被整个覆盖。
        """
        text = updater.BAT
        self.assertNotIn('"%DST%\\data"', text,
                         "/XD 又写成目的路径了 —— 这条在真机上不生效，会覆盖用户笔记库")

    def test_launch_builds_quoted_cmdline(self):
        """`cmd /c "a b\\x.bat" 参数` 会被 cmd 剥引号剥坏，必须写成 ""…""。"""
        work = os.path.join(self.tmp, "w")
        src = os.path.join(self.tmp, "pkg", "素笺")
        os.makedirs(src)
        captured = {}

        def fake_popen(line, **kw):
            captured["line"] = line
            captured["kw"] = kw
            return mock.Mock()

        with mock.patch.object(updater.subprocess, "Popen", fake_popen):
            bat = updater.launch_apply(src, os.path.join(self.tmp, "dst"),
                                       os.path.join(src, "素笺.exe"),
                                       workdir=work, pid=4321)
        line = captured["line"]
        self.assertTrue(line.startswith('cmd /c ""'), line)
        self.assertTrue(line.endswith('""'), line)
        self.assertIn(" 4321 ", line)
        self.assertIn(os.path.abspath(src), line)
        self.assertTrue(os.path.isfile(bat))
        # 三个标准流都断开：否则子进程会攥着我们的管道，进程退不掉
        self.assertIs(captured["kw"]["stdin"], updater.subprocess.DEVNULL)


# ------------------------------ 收尾 ------------------------------

class TestHousekeeping(_Tmp):
    def test_clean_update_dirs(self):
        root = updater.update_root()
        for name in ("v1.0.0", "v1.1.0"):
            os.makedirs(os.path.join(root, name))
            with open(os.path.join(root, name, "x"), "w") as f:
                f.write("x")
        updater.clean_update_dirs(keep=("v1.1.0",))
        self.assertEqual(os.listdir(root), ["v1.1.0"])

    def test_clean_keeps_downloaded_package(self):
        """下好但还没装的包不能删：用户关了程序再进来，不该让他重下 26MB。"""
        root = updater.update_root()
        keep_dir = os.path.join(root, "v1.1.0")
        os.makedirs(keep_dir)
        with open(os.path.join(keep_dir, "package.zip"), "wb"):
            pass
        os.makedirs(os.path.join(root, "v1.0.0"))       # 失败留下的空壳
        updater.clean_update_dirs()
        self.assertEqual(os.listdir(root), ["v1.1.0"])

    def test_clean_drops_current_version_package(self):
        """刚装上的那个版本：包已经落进程序目录了，临时目录里那份必须删掉
        （它带着 package.zip，本来会被"保留下载"那条规则留下占 26MB）。"""
        root = updater.update_root()
        for name in ("v1.1.0", "v2.0.0"):
            d = os.path.join(root, name)
            os.makedirs(d)
            with open(os.path.join(d, "package.zip"), "wb"):
                pass
        updater.clean_update_dirs(drop_version="1.1.0")   # 不带 v 也要认出来
        self.assertEqual(os.listdir(root), ["v2.0.0"])

    def test_read_apply_error(self):
        self.assertIsNone(updater.read_apply_error())
        root = updater.update_root()
        os.makedirs(os.path.join(root, "v1.1.0"))
        path = os.path.join(root, "v1.1.0", "update_error.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("robocopy failed with errorlevel=16")
        got = updater.read_apply_error()
        self.assertEqual(got[0], path)
        self.assertIn("errorlevel=16", got[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
