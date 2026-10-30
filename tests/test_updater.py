# -*- coding: utf-8 -*-
"""检查更新（updater.py）的单测 —— 全部离线，一个请求都不发。

三条纪律里最难测也最该测的是第三条「永远不抛异常」：更新检查把主界面弄崩，
是这类功能最丢人的失败方式。所以下面用 mock 把 fetch_latest 打成各种烂样子,
逐一确认 check() 都能返回一个带中文原因的结果。

    python tests\\test_updater.py
或：
    python -m unittest discover -s tests -v
"""
import os
import socket
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import updater


def release(tag="v1.1.0", assets=None, body="修了三个 bug"):
    """造一份形状接近 GitHub 真实返回的 release。"""
    return {
        "tag_name": tag,
        "html_url": "https://github.com/simonking-ws/local-notes/releases/tag/" + tag,
        "body": body,
        "published_at": "2026-10-30T04:00:00Z",
        "prerelease": False,
        "assets": assets if assets is not None else [
            {"name": "local-notes.zip", "size": 26231406,
             "browser_download_url": "https://example.com/a.zip",
             "digest": "sha256:68f2637abf4e9c68"},
        ],
    }


class TestParseRelease(unittest.TestCase):
    def test_keeps_fields_we_need(self):
        r = updater.parse_release(release())
        self.assertEqual(r["version"], "v1.1.0")
        self.assertEqual(r["body"], "修了三个 bug")
        self.assertTrue(r["html_url"].endswith("v1.1.0"))
        self.assertEqual(len(r["assets"]), 1)
        # digest 是 P1 校验下载包要用的，这里必须原样带过来
        self.assertEqual(r["assets"][0]["digest"], "sha256:68f2637abf4e9c68")

    def test_missing_optional_fields_are_blank(self):
        # GitHub 的返回结构改过好几轮，缺字段只能少显示一行，不能 KeyError
        r = updater.parse_release({"tag_name": "1.2.0"})
        self.assertEqual(r["body"], "")
        self.assertEqual(r["assets"], [])
        self.assertEqual(r["published"], "")
        # html_url 缺失时兜底到 releases 列表页，而不是空串
        self.assertTrue(r["html_url"].endswith("/releases"))

    def test_no_tag_is_an_error(self):
        # 没有版本号就无法比较；硬报「已是最新」是撒谎
        for bad in ({}, {"tag_name": ""}, {"tag_name": "   "}):
            with self.assertRaises(updater.UpdateError):
                updater.parse_release(bad)

    def test_non_dict_is_an_error(self):
        for bad in (None, [], "{}", 42):
            with self.assertRaises(updater.UpdateError):
                updater.parse_release(bad)

    def test_assets_with_junk_entries(self):
        # assets 里混进非 dict 也不能崩
        r = updater.parse_release(release(assets=[
            "garbage", {"name": "x.zip", "size": 1, "browser_download_url": "u"}]))
        self.assertEqual(len(r["assets"]), 1)


class TestPickAsset(unittest.TestCase):
    def test_biggest_zip_wins(self):
        r = updater.parse_release(release(assets=[
            {"name": "a.zip", "size": 10, "browser_download_url": "u1"},
            {"name": "b.zip", "size": 99, "browser_download_url": "u2"},
        ]))
        self.assertEqual(updater.pick_asset(r)["name"], "b.zip")

    def test_ignores_non_zip_and_unuploaded(self):
        # 源码包 / 没传完的条目不能当成更新包
        r = updater.parse_release(release(assets=[
            {"name": "source.tar.gz", "size": 999},
            {"name": "notes.zip", "size": 5, "browser_download_url": ""},
        ]))
        self.assertIsNone(updater.pick_asset(r))

    def test_none_when_no_assets(self):
        self.assertIsNone(updater.pick_asset(updater.parse_release(release(assets=[]))))


class TestCheckNeverRaises(unittest.TestCase):
    """check() 必须吞掉一切 —— 界面线程只该看到一条中文原因。"""

    def _run(self, side_effect):
        with mock.patch.object(updater, "fetch_latest", side_effect=side_effect):
            return updater.check()

    def test_network_down(self):
        res = self._run(updater.UpdateError("连不上 GitHub，请检查网络或代理设置。"))
        self.assertEqual(res["state"], "error")
        self.assertIn("连不上", res["error"])

    def test_timeout(self):
        res = self._run(socket.timeout("timed out"))
        # 未知异常也要有兜底文案，不能是空串
        self.assertEqual(res["state"], "error")
        self.assertTrue(res["error"])

    def test_unexpected_exception(self):
        res = self._run(RuntimeError("boom"))
        self.assertEqual(res["state"], "error")
        self.assertIn("boom", res["error"])

    def test_newer(self):
        with mock.patch.object(updater, "fetch_latest",
                               return_value=updater.parse_release(release("v9.9.9"))):
            res = updater.check()
        self.assertEqual(res["state"], "new")
        self.assertEqual(res["version"], "v9.9.9")

    def test_same_version_is_latest(self):
        with mock.patch.object(updater, "fetch_latest",
                               return_value=updater.parse_release(release("1.0"))):
            res = updater.check()
        self.assertEqual(res["state"], "latest")

    def test_older_is_latest(self):
        with mock.patch.object(updater, "fetch_latest",
                               return_value=updater.parse_release(release("0.1"))):
            res = updater.check()
        self.assertEqual(res["state"], "latest")


class TestHttpErrors(unittest.TestCase):
    """HTTP 状态码要翻译成人话，别把 'HTTP Error 403' 直接丢给用户。"""

    def _http_error(self, code):
        import urllib.error
        return urllib.error.HTTPError(updater.API, code, "msg", {}, None)

    def test_404_means_no_release(self):
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=self._http_error(404)):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.fetch_latest()
        self.assertIn("还没有发布过版本", str(cm.exception))

    def test_403_is_rate_limit(self):
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=self._http_error(403)):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.fetch_latest()
        self.assertIn("频繁", str(cm.exception))

    def test_500_is_generic(self):
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=self._http_error(500)):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.fetch_latest()
        self.assertIn("500", str(cm.exception))

    def test_bad_json(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"<html>not json</html>"
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.fetch_latest()
        self.assertIn("看不懂", str(cm.exception))


class TestRequestHeaders(unittest.TestCase):
    def test_user_agent_carries_no_identity(self):
        """UA 里只能有应用名和版本号 —— 这是「不发送任何信息」那条纪律的落点。"""
        cap = {}

        def fake(req, timeout=None):
            cap["req"] = req
            raise updater.UpdateError("stop here")

        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=fake):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_latest()

        ua = cap["req"].get_header("User-agent") or cap["req"].headers.get("User-agent")
        from version import __version__
        self.assertEqual(ua, "Sujian/" + __version__)
        # 不带任何隐私字段
        joined = " ".join("%s=%s" % (k, v) for k, v in cap["req"].headers.items())
        for leak in ("hostname", os.environ.get("COMPUTERNAME", "\x00"), "user="):
            self.assertNotIn(leak.lower(), joined.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
