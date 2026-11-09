# -*- coding: utf-8 -*-
"""PicList 图床上传（零依赖：只用标准库 urllib / json）。

⚠️ **不能走命令行**：PicList 桌面版是 Electron 应用，`PicList.exe upload <路径>`
里的 `upload` 会被 Electron 当成「要运行的 JS 入口」，直接报
`Cannot find module '...\\upload'` —— stdout 全是这条报错、没有任何 URL。
（踩过：设置窗「测试上传」一直提示「PicList 没有返回图片链接：无输出」。）

桌面版真正对外提供的上传通道是它**内置的 HTTP Server**（PicList 里
「设置 → 内置 Server」，默认 127.0.0.1:36677）：

    POST http://127.0.0.1:36677/upload
    {"list": ["D:/.../a.png"]}
    → {"success": true, "result": ["https://.../a.png"]}

沿用 PicList 里已配好的图床，返回图片 URL。以下是实测约束：

  * 只认 http(s) URL：失败时 result 可能是本地路径/错误文案，当成 URL 存进
    正文会渲染成断图。
  * 必须设 timeout：图床卡住时 server 可能一直不回，绝不能让主界面死等 ——
    调用方还要放到后台线程里（见 app_actions 的接线）。
  * Server 没开 / 端口改了：给出明确指引，别只说「失败」。
"""
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
_DEFAULT_TIMEOUT = 30.0
# PicList 内置 Server 的默认端口（用户可在 PicList 设置里改，故 settings 里可选
# 覆盖 piclist_port；这里只是兜底）。
_DEFAULT_PORT = 36677


class UploadError(Exception):
    """上传失败（Server 没开 / 超时 / 返回内容里没有 URL）。"""


def configured(settings):
    """设置里有没有一个像样的 exe 路径。

    exe 路径仍然当「启用了图床」的总开关用（设置窗靠它显示「当前：图床上传 /
    本机保存」）；上传本身走 Server，但 exe 路径保留着，方便用户在 PicList
    没启动时知道程序装在哪。
    """
    exe = (settings or {}).get("piclist_exe") or ""
    return bool(str(exe).strip())


def exe_path(settings):
    p = str((settings or {}).get("piclist_exe") or "").strip().strip('"')
    return os.path.normpath(p) if p else ""


def server_port(settings):
    """PicList 内置 Server 端口：settings["piclist_port"] 优先，否则默认 36677。"""
    raw = (settings or {}).get("piclist_port")
    try:
        port = int(raw)
        if 0 < port < 65536:
            return port
    except (TypeError, ValueError):
        pass
    return _DEFAULT_PORT


def server_alive(settings, timeout=2.0):
    """PicList 内置 Server 是否在跑（/heartbeat 回 success）。不上传，只探活。"""
    port = server_port(settings)
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/heartbeat" % port, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
            return bool(data.get("success"))
    except Exception:
        return False


def parse_url(output):
    """从 PicList 的输出里抠出第一个 http(s) URL。"""
    if not output:
        return ""
    m = _URL_RE.search(str(output))
    return m.group(0).rstrip(".,;") if m else ""


def upload(settings, image_path, timeout=_DEFAULT_TIMEOUT):
    """调用 PicList 内置 Server 上传一张本地图片，成功返回 URL，失败抛 UploadError。

    image_path 必须是磁盘上真实存在的文件（通常是 data/media/<hash>.png）。
    本函数不删本地文件 —— 删不删由调用方在确认拿到 URL 之后决定。
    """
    if not os.path.isfile(image_path):
        raise UploadError("要上传的图片不存在：%s" % image_path)

    port = server_port(settings)
    if not server_alive(settings):
        exe = exe_path(settings)
        hint = "请先启动 PicList，并在「设置 → 内置 Server」里确认已开启。"
        if exe and not os.path.isfile(exe):
            hint += "\n配置的 PicList 路径不存在：%s" % exe
        raise UploadError(
            "连不上 PicList 内置 Server（127.0.0.1:%d）。\n%s" % (port, hint))

    body = json.dumps({"list": [image_path]}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/upload" % port, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except socket.timeout:
        raise UploadError("PicList 上传超时（%g 秒），请检查图床配置。" % timeout)
    except urllib.error.HTTPError as e:
        text = ""
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise UploadError("PicList Server 返回 HTTP %s：%s"
                          % (e.code, text.strip()[:160] or "无内容"))
    except urllib.error.URLError as e:
        raise UploadError("连不上 PicList 内置 Server（127.0.0.1:%d）：%s"
                          % (port, getattr(e, "reason", e)))
    except OSError as e:
        raise UploadError("连接 PicList 内置 Server 失败：%s" % e)

    # 返回形如 {"success":true,"result":["https://..."]}；失败时 result 里可能
    # 是错误文案、success 为 false。
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        raise UploadError("PicList 返回的不是有效 JSON：%s" % (raw.strip()[:160] or "无输出"))

    result = data.get("result")
    if isinstance(result, list):
        text = "\n".join(str(x) for x in result)
    else:
        text = str(result or "")
    url = parse_url(text)
    if not data.get("success") or not url:
        msg = text.strip()[:160] or json.dumps(data, ensure_ascii=False)[:160]
        raise UploadError("PicList 上传失败：%s" % msg)
    return url


def _default_test_image():
    """测试上传用的样图：应用自带的 icon/app_icon.png。

    路径口径和 theme._res_dir() 完全一致（源码运行是项目根 icon/，打包后是
    _MEIPASS/icon）。piclist 是零依赖模块，不 import theme，这里只用 os/sys
    自己算，别为了一个路径把 tkinter 依赖引进来。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "icon", "app_icon.png")


def test_upload(settings, timeout=_DEFAULT_TIMEOUT):
    """设置窗「测试上传」用：上传应用自带的 app_icon.png，返回 URL。

    以前是标准库现拼一张 1×1 临时 PNG：太小、纯色，图床要么当无效图拒掉，
    要么传上去也看不出效果。换成一张真实存在的示例图，测试的就是「PicList
    能不能正常传一张图」这件事本身，也不碰用户的 media/。
    """
    path = _default_test_image()
    if not os.path.isfile(path):
        raise UploadError("找不到测试图片：%s" % path)
    return upload(settings, path, timeout=timeout)
