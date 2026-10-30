# -*- coding: utf-8 -*-
"""素笺 —— 检查更新与自动更新（P0 检查 / P1 下载、校验、替换）。

分层上跟 store.py 一样**不 import tkinter**：网络与解析逻辑能被单测直接调用，
界面只负责把结果画出来。

三条纪律，写在最前面：

1. **只在用户点「检查更新」时才联网**。这个应用对外的一句承诺是「不联网」，
   现在放宽成「你不点就不联网」，所以绝不做什么启动自动检查 —— 哪天真要做，
   得先改 README 和关于弹窗里的文案，不能偷偷发请求。
2. **不发送任何信息**。请求头里只有 UA（GitHub 强制要求，缺了直接 403），
   UA 里只有应用名和版本号，没有设备 ID、没有笔记内容、没有使用统计。
3. **永远不抛异常**。`check()` 吞掉所有错误、翻译成人话返回。更新检查把主
   界面弄崩，是这类功能最丢人的失败方式。

P1 加了一条新纪律：**绝不碰用户的数据目录**。替换过程发生在进程已经退出之后，
那时候没有任何代码能替用户挡一下，所以「不覆盖 data/」这件事只能靠 robocopy
的排除参数和下面这几处体检（见 `prepare()` 里对安装包内含 `data/` 的拦截）。
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

from version import __version__, is_newer

# 仓库固定，不做成可配置项：这个应用没有多源分发的需求，多一个可配置项就
# 多一处「被改坏后查不出原因」的地方。
REPO = "simonking-ws/local-notes"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
FALLBACK_URL = "https://github.com/%s/releases" % REPO

# 8 秒：GitHub API 在本机实测 0.8s，8 秒已经足够容忍一次抖动；
# 再长用户就会以为「点了没反应」。超时是必须的 —— 没有超时的 urlopen
# 在断网/被墙时会挂到 TCP 自己放弃（几分钟），界面上的「检查中…」会一直转。
TIMEOUT = 8
# 下载包的形状：Release 里除了 zip 还可能有源码包（GitHub 自动附带的
# tarball/zipball），那些不是可执行程序，不能拿来当更新包。
ASSET_SUFFIX = ".zip"


class UpdateError(Exception):
    """检查更新失败。message 是**直接给用户看**的中文，所以别放堆栈和英文。"""


def parse_release(raw):
    """GitHub 的 release JSON → 固定形状的 dict。

    纯函数，不打网络，测试全靠它。字段取不到就给空串/空列表，**不 KeyError**：
    GitHub 的返回结构改过好几轮，缺字段时宁可少显示一行，也不能让「检查更新」
    变成「程序出错」。
    """
    if not isinstance(raw, dict):
        raise UpdateError("GitHub 返回的内容看不懂。")

    tag = str(raw.get("tag_name") or "").strip()
    if not tag:
        # 没有版本号就无法比较，只能算失败；硬报「已是最新」是撒谎
        raise UpdateError("这次发布没有写版本号，无法比较。")

    assets = []
    for a in raw.get("assets") or []:
        if not isinstance(a, dict):
            continue
        assets.append({
            "name": str(a.get("name") or ""),
            "size": int(a.get("size") or 0),
            "url": str(a.get("browser_download_url") or ""),
            # GitHub 会给每个上传的资产算 sha256（形如 "sha256:68f2…"）。
            # 有了它就不必再单独发布一个 checksum 文件 —— P1 下载完直接比这个。
            "digest": str(a.get("digest") or ""),
        })

    return {
        "version": tag,
        "html_url": str(raw.get("html_url") or FALLBACK_URL),
        "body": str(raw.get("body") or "").strip(),
        "published": str(raw.get("published_at") or ""),
        "prerelease": bool(raw.get("prerelease")),
        "assets": assets,
    }


def pick_asset(release):
    """从 assets 里挑出「给人用的那个包」：名字以 .zip 结尾、体积最大的。

    按体积挑而不是按名字挑，是因为命名会随发版习惯漂移（现在叫
    local-notes.zip，以后想改成 素笺-v1.1.0-win64.zip 都不影响这里）。
    源码包（zipball）不会出现在 assets 里，所以不会被误选。
    """
    cands = [a for a in release.get("assets") or []
             if a.get("name", "").lower().endswith(ASSET_SUFFIX) and a.get("url")]
    if not cands:
        return None
    return max(cands, key=lambda a: a.get("size") or 0)


def fetch_latest(timeout=TIMEOUT):
    """真正发请求的唯一一处。成功返回解析后的 dict，失败抛 UpdateError。"""
    req = urllib.request.Request(API, headers={
        # GitHub 的 API 强制要求带 UA，不带直接 403（历史上是 403，
        # 现在文档写「请求会被拒绝」）。UA 里只放应用名和版本，不带任何标识。
        "User-Agent": "Sujian/" + __version__,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        # HTTPError 是 URLError 的子类，必须排在它前面捕
        if e.code == 404:
            raise UpdateError("这个仓库还没有发布过版本。")
        if e.code in (403, 429):
            # 未认证的 GitHub API 按 IP 限流 60 次/小时，办公室/校园网出口
            # 共享一个 IP 时很容易撞上
            raise UpdateError("请求太频繁了，过一会儿再试。")
        raise UpdateError("GitHub 返回了错误（HTTP %d）。" % e.code)
    except (urllib.error.URLError, socket.timeout, OSError):
        # 断网、DNS 失败、被墙、代理没开、超时 —— 对用户是同一件事
        raise UpdateError("连不上 GitHub，请检查网络或代理设置。")

    try:
        raw = json.loads(payload.decode("utf-8"))
    except Exception:
        raise UpdateError("GitHub 返回的内容看不懂。")
    return parse_release(raw)


def check(timeout=TIMEOUT):
    """检查是否有新版本。**不抛异常**，返回固定形状的 dict：

    ``{"state": "new" | "latest" | "error", ...}``
     - new    : 有新版，另带 version / html_url / body / assets
     - latest : 已是最新
     - error  : 失败，带 error（中文原因）
    """
    try:
        info = fetch_latest(timeout)
    except UpdateError as e:
        return {"state": "error", "version": "", "error": str(e)}
    except Exception as e:                     # noqa: BLE001
        # 兜底：上面没预料到的异常也绝不能漏到界面线程里去
        return {"state": "error", "version": "",
                "error": "检查更新时出错：%s" % e}

    info["state"] = "new" if is_newer(info["version"]) else "latest"
    return info


# ======================= P1：下载 → 校验 → 解包 → 替换 =======================
#
# 整件事的顺序不能变：
#   应用内：下载 zip → 算 sha256 比对 GitHub 给的 digest → 解包 → 找人话目录
#   进程外：等本进程退出 → robocopy 覆盖（排除 data）→ 重新拉起
#
# 为什么必须"进程外"：Windows 上正在运行的 .exe 与其加载的 .dll 既不能删也不能
# 覆盖，连它所在的目录都不能改名。没有任何用户态技巧能绕开这一点，所以自动更新
# 在 Windows 上必然是两段式，差别只在第二段用什么执行（批处理 / 安装包 / 服务）。

APP_EXE = "素笺.exe"
CHUNK = 65536
# 下载超时按"两次读之间的静默时间"算（socket 层的 timeout 只管单次 recv），
# 所以给 20 秒：26MB 的包在国内实测 139 KB/s，单块 64KB 约 0.5 秒，20 秒足够
# 容忍一次抖动，又不至于让"取消"点了半天没反应。
DL_TIMEOUT = 20


class Cancelled(UpdateError):
    """用户中途取消。继承 UpdateError，是为了让「失败一律是 UpdateError」这条不破。"""


def is_frozen():
    """是不是打包后的 exe 在跑。源码运行时没有可替换的 exe，不能自动更新。"""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """当前程序所在目录（打包后 = exe 目录）。源码运行返回空串。"""
    if not is_frozen():
        return ""
    return os.path.dirname(os.path.abspath(sys.executable))


def update_root():
    """更新包的落脚处：%LOCALAPPDATA%\\Sujian\\update。

    **不能放程序目录**：用户完全可能把程序解压到 C:\\Program Files 下面，那里
    普通进程没有写权限 —— 下载会在一开始就以 PermissionError 失败，而报错文案
    还得解释"为什么我的文档目录不能写"，纯属自找。
    """
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("TEMP")
            or os.path.expanduser("~"))
    return os.path.join(base, "Sujian", "update")


def human_size(n):
    """给界面看的体积。0 或负数给空串，免得"（0 B）"这种字样出现在弹窗里。"""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit, div in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2), ("KB", 1024.0)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % n


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def parse_digest(s):
    """GitHub 的 digest 长这样：``sha256:68f2637a…``。不认识的一律返回空串。"""
    s = str(s or "").strip().lower()
    if s.startswith("sha256:"):
        s = s[7:]
    return s if len(s) == 64 and all(c in "0123456789abcdef" for c in s) else ""


def download(url, dest, total=0, progress=None, timeout=DL_TIMEOUT):
    """把 url 下到 dest，返回**整个文件**的 sha256。支持断点续传与取消。

    续传靠 ``<dest>.part``：没下完就留着，下次从 `.part` 的长度接着要
    ``Range: bytes=N-``。这在国内网络下不是锦上添花 —— 26MB 的包要下三分钟，
    中途断一次就从头来的话，用户第三次就再也不想点这个按钮了。

    ⚠️ 续传时**已下好的那段也要重新喂给哈希**，否则续传后算出来的指纹必然
    和 GitHub 的 digest 对不上，表现是"明明下载成功却校验失败"，而且只有
    断线过的用户会遇到 —— 最难查的那一类。
    """
    progress = progress if progress is not None else {}
    folder = os.path.dirname(os.path.abspath(dest))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    part = dest + ".part"

    have = os.path.getsize(part) if os.path.exists(part) else 0
    if total and have > total:
        # .part 比远端文件还长（换过一次包）：这个续传点没意义，丢掉重来
        have = 0
    if not have:
        try:
            os.remove(part)
        except OSError:
            pass

    def _open():
        headers = {"User-Agent": "Sujian/" + __version__}
        if have:
            headers["Range"] = "bytes=%d-" % have
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        resp = _open()
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:
            # 本机 .part 比远端文件还长（换过一次包）：丢掉重来
            try:
                os.remove(part)
            except OSError:
                pass
            have = 0
            resp = _open()
        elif e.code in (403, 429):
            raise UpdateError("下载被 GitHub 限流了，过一会儿再试。")
        else:
            raise UpdateError("下载失败（HTTP %d）。" % e.code)
    except (urllib.error.URLError, socket.timeout, OSError):
        raise UpdateError("连不上 GitHub，请检查网络或代理设置。")

    h = hashlib.sha256()
    try:
        with resp:
            code = resp.getcode()
            # 只有 206 才算续传成功。服务器不理 Range 直接回 200 时，流的开头
            # 是文件第 0 字节，此时必须从头写 —— 否则文件就是"新内容接在旧内容
            # 后面"，长度对、哈希错，最后表现为校验失败。
            if have and code != 206:
                have = 0
            if have:
                with open(part, "rb") as f:
                    for block in iter(lambda: f.read(CHUNK), b""):
                        h.update(block)
            clen = str(resp.headers.get("Content-Length") or "")
            if clen.isdigit():
                total = have + int(clen)
            progress["done"] = have
            progress["total"] = total or 0
            with open(part, "ab" if have else "wb") as f:
                while True:
                    if progress.get("cancel"):
                        raise Cancelled("已取消下载。")
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    f.write(block)
                    h.update(block)
                    have += len(block)
                    progress["done"] = have
    except Cancelled:
        raise
    except (urllib.error.URLError, socket.timeout, OSError):
        # 保留 .part，下次接着下
        raise UpdateError("下载中断了，请重试（会从断点继续）。")

    if total and have < total:
        raise UpdateError("下载不完整（%s / %s），请重试。"
                          % (human_size(have), human_size(total)))
    os.replace(part, dest)
    return h.hexdigest()


def _safe_extract(zf, dest):
    """解包，并挡掉 zip-slip（成员名里带 ``..`` 或绝对路径能写到 dest 之外）。

    这不是假想的攻击面：我们要解的是一个从网上拿回来的压缩包，万一账号被盗、
    发布账号被换，这个包就是直接落地的代码。这一步只是把"最省事的越界写法"
    挡掉，真正的信任边界仍然只有一条 —— 校验 sha256 和 HTTPS。
    """
    root = os.path.abspath(dest)
    for m in zf.infolist():
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or ":" in name:
            raise UpdateError("安装包里有可疑路径（%s），已中止。" % m.filename)
    zf.extractall(root)


def package_root(folder, exe_name=APP_EXE, max_depth=3):
    """在解出来的目录树里找「主程序所在的那一层」。

    为什么不能写死结构：Release 里那个 zip 是作者手动打的，里面可能一层
    ``素笺/``（PyInstaller 的 dist 目录原样打包），也可能是把文件直接铺在 zip
    根上。写死一层就会在"作者换个打包方式"时静默失败，所以找的是**那个 exe**。
    """
    folder = os.path.abspath(folder)
    for depth_root, dirs, files in os.walk(folder):
        rel = os.path.relpath(depth_root, folder)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs[:] = []
            continue
        for name in files:
            if name.lower() == exe_name.lower():
                return depth_root
    return ""


def prepare(release, progress=None):
    """下载 + 校验 + 解包，返回 ``{version, dir, src, exe, sha256, verified}``。

    ``src`` 是"新版本文件所在的那一层"，也就是 robocopy 的源；``dir`` 是这个
    版本专用的工作目录（apply 脚本放这里，下次启动会清掉）。
    """
    progress = progress if progress is not None else {}
    asset = pick_asset(release)
    if not asset or not asset.get("url"):
        raise UpdateError("这次发布没有可下载的安装包。")

    ver = str(release.get("version") or "").strip() or "update"
    safe = "".join(c for c in ver if c.isalnum() or c in "._-") or "update"
    # 顺手清掉更早版本的工作目录（留着这个版本的，里面可能有上一次下好的包）
    clean_update_dirs(keep=(safe,))
    work = os.path.join(update_root(), safe)
    os.makedirs(work, exist_ok=True)
    zp = os.path.join(work, "package.zip")

    want = parse_digest(asset.get("digest"))
    got = ""
    if os.path.exists(zp):
        got = sha256_of(zp)
        # 已经下好且对得上（或远端没给 digest）就不再下一遍：用户点了取消 /
        # 上次校验失败后重试时，能省掉一次三分钟的等待。
        if not want or got == want:
            progress["done"] = progress["total"] = os.path.getsize(zp)
        else:
            try:
                os.remove(zp)
            except OSError:
                pass
            got = ""

    if not got:
        got = download(asset["url"], zp, total=asset.get("size") or 0,
                       progress=progress)

    progress["phase"] = "verify"
    verified = None
    if want:
        if got != want:
            try:
                os.remove(zp)
            except OSError:
                pass
            raise UpdateError("下载的安装包校验不通过（可能没下完或被替换过），请重试。")
        verified = True

    progress["phase"] = "extract"
    pkg = os.path.join(work, "pkg")
    shutil.rmtree(pkg, ignore_errors=True)
    try:
        with zipfile.ZipFile(zp) as zf:
            _safe_extract(zf, pkg)
    except UpdateError:
        raise
    except (zipfile.BadZipFile, OSError):
        raise UpdateError("安装包解压失败（文件可能已损坏），请重试。")

    src = package_root(pkg)
    if not src:
        raise UpdateError("安装包里没找到 %s，这不是可用的更新包。" % APP_EXE)
    # 体检：包里若夹带 data/，robocopy 会把它铺到用户的 data/ 上（overwrite 部分
    # 正好就是笔记库）。build.py 不会打进 data/，这里是防"手动打包时打错目录"。
    if os.path.isdir(os.path.join(src, "data")):
        raise UpdateError("安装包里混进了 data 目录，为保护笔记已中止更新。")

    return {"version": ver, "dir": work, "src": src,
            "exe": os.path.join(src, APP_EXE),
            "sha256": got, "verified": verified}


# 批处理模板。**全 ASCII**：cmd 按当前控制台代码页逐行读 .bat 文件，文件里若
# 有中文，在没切到 65001 的机器上就是乱码（`chcp 65001` 写在第一行也只能救
# 它后面的行）。路径全部走命令行参数（CreateProcessW 传的是 UTF-16，中文安全），
# 所以这个文件一个字节都不用变。
BAT = r"""@echo off
rem ---- Sujian auto-update helper. Generated file, do not edit. ----
rem args: 1=pid  2=source dir  3=install dir  4=exe to launch
setlocal enableextensions
set "PID=%~1"
set "SRC=%~2"
set "DST=%~3"
set "EXE=%~4"
set "LOG=%~dp0update_error.log"
set /a TRY=0
:wait
rem the app is still running and holds its own exe/dlls open
tasklist /FI "PID eq %PID%" /NH 2>nul | find "%PID%" >nul
if errorlevel 1 goto install
set /a TRY+=1
if %TRY% GEQ 150 goto force
rem ping instead of timeout: "timeout" refuses to run when stdin is redirected
ping -n 2 127.0.0.1 >nul
goto wait
:force
rem two and a half minutes and still alive: the app is stuck, take it down
taskkill /PID %PID% /F >nul 2>nul
ping -n 3 127.0.0.1 >nul
:install
rem /E copies; /XD keeps the notes database out of the copy. The excluded path
rem MUST be the SOURCE one: robocopy matches /XD paths against the source tree,
rem so writing the DESTINATION prefix here silently does nothing (verified by
rem poisoning a package with data\notes_data.db: the user's db got overwritten).
rem A bare "/XD data" also works but would skip _internal\data, which a future
rem build may legitimately ship -- so pin the exact source path instead.
robocopy "%SRC%" "%DST%" /E /XD "%SRC%\data" /R:3 /W:1 /NJH /NJS /NDL /NFL >nul
if errorlevel 8 goto fail
start "" "%EXE%"
goto quit
:fail
>"%LOG%" echo robocopy failed with errorlevel=%ERRORLEVEL%
rem ONLY an ascii, path-free line: cmd writes this file in the OEM codepage,
rem so a Chinese path here would come back as mojibake in the app's dialog
start "" "%EXE%"
:quit
rem deleting a running .bat fails; jump to a missing label so cmd drops the handle
(goto) 2>nul & del "%~f0"
"""


def write_apply_bat(workdir):
    """把替换脚本写到 workdir 里，返回路径。以 ASCII + CRLF 落盘（cmd 的要求）。"""
    if not os.path.isdir(workdir):
        os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, "apply_update.bat")
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(BAT)
    return path


def launch_apply(src, dst, exe, workdir=None, pid=None):
    """起一个脱离本进程的 cmd 去执行替换脚本，然后调用方应当立刻退出。

    为什么要这么绕：Windows 不允许覆盖/删除正在运行的 exe，所以"更新"这件事
    只能由另一个进程在本进程退出之后做。而 cmd 又必须是**独立**的（不能被我们
    的进程树带走、不能继承控制台），所以 creationflags 用 DETACHED_PROCESS。

    ⚠️ 命令行是手工拼的字符串，不是列表。`cmd /c "带空格的路径" 参数…` 会让
    cmd 按它自己那套历史规则剥引号（"more than 2 quotes" 分支会把首尾引号一起
    砍掉），结果整条命令被拗成别的东西。标准解法是外面再包一层引号：
    `cmd /c ""路径" 参数…"`，剥掉一层后正好是干净的写法。
    """
    workdir = workdir or os.path.dirname(os.path.abspath(src))
    bat = write_apply_bat(workdir)
    pid = os.getpid() if pid is None else int(pid)
    line = 'cmd /c ""%s" %d "%s" "%s" "%s""' % (
        bat, pid, os.path.abspath(src), os.path.abspath(dst), os.path.abspath(exe))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(line, cwd=workdir, close_fds=True,
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=flags)
    return bat


def read_apply_error():
    """上次替换失败了吗？返回 ``(日志路径, 内容)``，没有就返回 ``None``。

    失败信息不走弹窗（那时本进程已经退出了），批处理只留一个日志文件；
    新版/旧版下一次启动时把它读出来提示一次，然后删掉。
    """
    root = update_root()
    if not os.path.isdir(root):
        return None
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        path = os.path.join(root, name, "update_error.log")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            except OSError:
                text = ""
            return path, text
    return None


def clean_update_dirs(keep=(), keep_downloads=True, drop_version=""):
    """清掉旧版本的工作目录（安装包和解出来的文件都有一份，26MB 不能白占）。

    ``keep`` 里的是"正在用"的目录名。``keep_downloads`` 为真时，**下好了完整包**
    的目录不删 —— 用户可能下完没点「立即更新」就关了程序，下次进来还能直接复用
    （prepare 会先校验 sha256），不必再等三分钟重下一遍。

    ``drop_version`` 传当前版本号：名字相同的目录**一定删**。那是「刚装上的那个
    版本」的工作目录，安装包已经落进程序目录了，留着纯属占 26MB —— 而它恰好因为
    带 package.zip 会被上面那条保住，所以要单独开口子。

    删不掉就跳过：刚执行完的批处理可能还压在目录里，下次启动再清也是一样的。
    """
    root = update_root()
    if not os.path.isdir(root):
        return
    try:
        names = os.listdir(root)
    except OSError:
        return
    # 版本号可能带 v（release 的 tag 常写 v1.1.0），比较时一律去掉
    cur = str(drop_version or "").strip().lstrip("vV")
    for name in names:
        if name in keep:
            continue
        path = os.path.join(root, name)
        if cur and name.strip().lstrip("vV") == cur:
            shutil.rmtree(path, ignore_errors=True)
            continue
        if keep_downloads and os.path.isfile(os.path.join(path, "package.zip")):
            continue
        shutil.rmtree(path, ignore_errors=True)
