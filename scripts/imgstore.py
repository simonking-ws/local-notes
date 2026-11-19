# -*- coding: utf-8 -*-
"""素笺 —— 图片落盘层（零依赖：只用标准库，Pillow 可选）。

职责：把「一张图」变成一段能落库的 img 数据块，以及把 img 块变回磁盘文件。

数据块形状（与 ["hr"] / ["code", ...] 并列，属于正文里的结构项）：

    ["img", {"id": "i_xxx", "src": "media/<hash>.png",
             "w": 640, "h": 480, "alt": "截图"}]

src 只有两种形态：

  * ``media/<hash>.png`` —— 本地文件，相对 ``store.BASE``，**绝绝对路径**；
  * ``http://...`` / ``https://...`` —— 图床 URL（PicList 上传成功后写入）。

⚠️ 几条不能破的红线：

  * src 里只存相对路径。存绝对路径的话项目一搬家 / 换台机器就全断链；
    渲染一律走 ``src_path()``，它也会拒绝 ``..`` 逃出 media/ 的脏数据。
  * 文件名取**归一化后 PNG 字节**的 sha256 前 20 位 —— 同一张图无论从
    JPG / PNG / 剪贴板进来都落到同一个文件，天然去重，也让孤儿回收简单。
  * 有 Pillow 就把 JPG / WebP / GIF 转成 PNG、大图按需缩放；没有 Pillow 时
    只收 PNG（Tk 8.6 原生认 PNG/GIF），其它格式**明确拒绝**而不是硬塞进去
    —— 静默存一个 Tk 打不开的文件，用户要等重开笔记才发现图是空的。
  * 写文件走临时文件 + os.replace，避免写入中断留下半张图。
"""
import os
import io
import re
import hashlib
import struct
import uuid

KIND = "img"
MEDIA_DIR_NAME = "media"
PNG_SUFFIX = ".png"
REMOTE_RE = re.compile(r"^https?://", re.I)
# 显示上限：只用于渲染时缩放，不改磁盘原图（用户要的是文件存储，别擅自压画质）
DEFAULT_MAX_EDGE = 0
# 文件名白名单：sha256 前 20 位十六进制
_NAME_RE = re.compile(r"^[0-9a-f]{20}\.png$")


class UnsupportedImage(Exception):
    """图片格式 Tk 打不开，且没有 Pillow 可转。界面层据此提示用户。"""


def img_id():
    return "i_" + uuid.uuid4().hex[:12]


def is_remote(src):
    return bool(REMOTE_RE.match(str(src or "")))


def is_local(src):
    s = str(src or "")
    return bool(s) and not is_remote(s)


def _base(base=None):
    if base is not None:
        return base
    # 延迟 import：store 不 import imgstore，避免任何循环依赖
    import store
    return store.BASE


def media_dir(base=None):
    return os.path.join(_base(base), MEDIA_DIR_NAME)


def ensure_media_dir(base=None):
    d = media_dir(base)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def is_png(data):
    return bool(data) and data[:8] == b"\x89PNG\r\n\x1a\n"


def png_size(data):
    """从 PNG 的 IHDR 读 (宽, 高)；不是合法 PNG 就返回 (0, 0)。不依赖 Pillow。"""
    if not is_png(data) or len(data) < 24 or data[12:16] != b"IHDR":
        return (0, 0)
    try:
        w, h = struct.unpack(">II", data[16:24])
    except Exception:
        return (0, 0)
    return (int(w), int(h)) if w and h else (0, 0)


def normalize_image(data, max_edge=DEFAULT_MAX_EDGE):
    """任意图片字节 → (png_bytes, w, h)；无 Pillow 时只收 PNG。

    max_edge > 0 时按最长边等比缩放（截图贴进来动辄 4K，显示和内存都吃不消）。
    缩放只影响落盘文件；本函数不碰显示尺寸，渲染另有自适应逻辑。
    """
    data = data or b""
    if not data:
        raise UnsupportedImage("图片是空的。")
    try:
        from PIL import Image          # 可选依赖：本机 12.3.0，缺了也能跑 PNG
    except Exception:
        if is_png(data):
            w, h = png_size(data)
            return data, w, h
        raise UnsupportedImage("只有 PNG 能在未安装 Pillow 的环境里显示，"
                               "请先把图片转成 PNG 再插入。")

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise UnsupportedImage("这张图片读不出来：%s" % e)
    # RGB 保持原样（转成 RGBA 会让 PNG 白白胖一圈）；带透明/调色板的才转 RGBA
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    if max_edge and max_edge > 0 and max(im.size) > max_edge:
        try:
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        except Exception:
            im.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), int(im.width), int(im.height)


def save_image(data, base=None, max_edge=DEFAULT_MAX_EDGE,
               src_name=None, width=0, height=0):
    """把图片字节存进 media/，返回可落库的 dict（不含 id/alt）。

    同一张图重复插入只会有一个文件（hash 命名）；已存在时只复用路径，尺寸仍以
    本次传入为准（调用方可能指定了显示尺寸）。
    """
    png, w, h = normalize_image(data, max_edge=max_edge)
    if not w or not h:
        w, h = int(width or 0), int(height or 0)
    name = hashlib.sha256(png).hexdigest()[:20] + PNG_SUFFIX
    d = ensure_media_dir(base)
    path = os.path.join(d, name)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
    return {"src": MEDIA_DIR_NAME + "/" + name, "w": int(w), "h": int(h)}


def make_block(data, alt="", base=None, max_edge=DEFAULT_MAX_EDGE,
               width=0, height=0, ident=None):
    """一步造出完整的 img 数据块。"""
    d = save_image(data, base=base, max_edge=max_edge,
                   width=width, height=height)
    d.update({"id": ident or img_id(), "alt": str(alt or "")})
    return [KIND, d]


def src_path(src, base=None):
    """本地 src → 绝对路径；图床 URL / 非法路径返回 None。

    ⚠️ 只认 ``media/<name>`` 且 name 是 sha256 文件名。别图省事放开任意相对
    路径：脏备份里塞一个 ``media/../../notes_data.db`` 就能让渲染读出整个库。
    """
    if is_remote(src):
        return None
    s = str(src or "").replace("\\", "/")
    prefix = MEDIA_DIR_NAME + "/"
    if not s.startswith(prefix):
        return None
    name = s[len(prefix):]
    if not _NAME_RE.match(name):
        return None
    return os.path.join(media_dir(base), name)


def read_local(src, base=None):
    p = src_path(src, base)
    if not p or not os.path.isfile(p):
        return b""
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return b""


def remove_local(src, base=None):
    """图床上传成功后删本地文件。返回是否真的删掉了。"""
    p = src_path(src, base)
    if not p or not os.path.isfile(p):
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False


def _looks_like_image(raw):
    """按魔数认图（PNG / JPEG / GIF / BMP / WEBP）。"""
    head = bytes(raw[:16]) if raw else b""
    return (head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or head.startswith(b"GIF8")
            or head.startswith(b"BM")
            or (head.startswith(b"RIFF") and head[8:12] == b"WEBP"))


# 公开图片转存服务（只在前两级都取不到图时兜底）。
# 实测 2026-11-18：某图床 CDN 域名（files.seeusercontent.com）在本机网络里
# https 被 RST、http 被网关换成「URL过滤」页，两条路都死；而 weserv 直连可用，
# 把 host/path 交给它取回的 PNG 与原图字节完全一致。
IMAGE_PROXY = "https://images.weserv.nl/?url={}"


def _proxy_url(url, tpl=None):
    """把图片地址包成转存服务地址：host/path?query 整段交给它（带 ? 保留、& 编码）。"""
    from urllib.parse import quote, urlsplit
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if not p.netloc:
        return ""
    tail = p.netloc + p.path
    if p.query:
        tail += "?" + p.query
    return (tpl or IMAGE_PROXY).format(quote(tail, safe="/:@?"))


# 远程图下载上限：一个失控 / 恶意 URL 返回超大响应会在 resp.read() 处把内存
# 吃掉。正常图片远小于此，超限立即中止。
MAX_FETCH_BYTES = 40 * 1024 * 1024


def _http_open(u, timeout=20.0):
    """统一 HTTP 入口（独立成函数便于测试替换）。"""
    import urllib.request
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Content-Length 预检（多数服务器会给），省得已经下完才发现超限
        try:
            declared = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_FETCH_BYTES:
            raise ValueError("图片太大（>%d MB），已跳过。" % (MAX_FETCH_BYTES >> 20))
        chunks, total = [], 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FETCH_BYTES:
                raise ValueError("图片太大（>%d MB），已跳过。" % (MAX_FETCH_BYTES >> 20))
            chunks.append(chunk)
        return b"".join(chunks)


def fetch_bytes(url, timeout=20.0):
    """下载图床 URL 的原始字节（必须是图片）。失败抛异常，信息尽量说清原因。

    三件兜底，都是实测踩出来的（2026-11-18）：

    ① **https 握手被掐 → 降级 http 重试。** 本机直连某个图床时 TCP 443 连得上，
       但 TLS 握手被 RST（WinError 10054 / EOF in violation of protocol）；
       换成 http 明文往往就通了。图床图片是公开资源，丢加密没有实质损失。

    ② **拿到的字节不是图 → 也换 http 再试。** 有的网关不掐连接、而是回一个
       200 的「URL 过滤」HTML 页面：只按 HTTP 状态码判断会当成成功，一路走到
       normalize_image 才报「cannot identify image file」—— 用户看到这句话
       完全没法自查。这里按魔数验一遍，把「被拦截 / 链接失效 / 要代理」直接
       写进错误里。

    ③ **两条都死 → 公开图片转存服务兜底。** 有些域名是「按域名拦死」的：
       https 被 RST、http 被换成拦截页，本地再怎么写都取不到（改 DNS、加 header
       都没用，问题不在协议）。交给 images.weserv.nl 由它去取，回到的是同一张
       图。代价是图片地址会以明文发给第三方，所以只作最后一级，且在直连能取到
       时绝不经过它（内网图床/私有链接也不会被莫名转出去）。

    全部失败时优先抛「拿到了网页」那条（比 SSL 报错可自查），否则抛第一次的
    网络错误：那才是用户真正需要的线索（域名解析失败、真的断网、404 …）。
    """
    url = str(url or "").strip()
    if not is_remote(url):
        raise ValueError("不是 http(s) 地址：%s" % url)

    candidates = [url]
    if url.lower().startswith("https://"):
        candidates.append("http://" + url[len("https://"):])
    proxy = _proxy_url(url)
    if proxy:
        candidates.append(proxy)

    first_err = None
    web_err = None
    for u in candidates:
        try:
            raw = _http_open(u, timeout)
        except Exception as e:
            first_err = first_err or e
            continue
        if _looks_like_image(raw):
            return raw
        web_err = web_err or UnsupportedImage(
            "图床返回的是网页而不是图片：%s\n"
            "常见原因：网络拦截（单位/校园网关的 URL 过滤）、链接已失效、需要代理。"
            % u)
    # 「拿到了网页」比「握手失败」对用户有用得多（后者只会甩一句 SSL 报错）。
    tip = ("\n已依次试过直连、明文 http、公开图片代理，三条都不通 ——"
           "多半是本机网络把这个域名整体拦住了。可以换个图床，"
           "或把 PicList路径 留空、改存本机。")
    if web_err:
        raise UnsupportedImage(str(web_err) + tip)
    if first_err is not None:
        # 尽量保留原异常类型（调用方/日志可能认得），拼上行动指引；
        # 个别异常类型的构造签名不接受单字符串，那就原样抛。
        try:
            raise type(first_err)(str(first_err) + tip)
        except TypeError:
            raise first_err
    raise ValueError("下载失败：%s" % url)


def collect_refs(notes):
    """扫全库正文，返回所有**本地** img src 的集合（图床 URL 不在磁盘上，忽略）。"""
    refs = set()
    for n in notes or ():
        try:
            content = n.get("content") or ()
        except Exception:
            continue
        for it in content:
            if not isinstance(it, (list, tuple)) or len(it) < 2:
                continue
            if it[0] != KIND:
                continue
            d = it[1] if isinstance(it[1], dict) else {}
            src = str(d.get("src") or "")
            if is_local(src):
                refs.add(src)
    return refs


def notes_from_recycle(records):
    """回收站记录 → 仍在引用图片的 note 列表（供 GC 当作「不能删」的依据）。

    ⚠️ 回收站有两种 payload 形状，缺一就会把图误删、还原后空图：
      * 单条笔记：``recycle_put("note", note_dict)`` → payload 本身就是一条 note（有 content）；
      * 文件夹子树：``recycle_put("folder", {"folders":[...], "notes":[...]})`` → 笔记在 notes 键。
    两种都要收进来。全量测试里真窗口只换 store._STORE、不换 BASE，早先只认 notes
    键时单条快照的图会在下一次 GC 里被清掉。
    """
    out = []
    for rec in records or ():
        try:
            p = rec.get("payload") or {}
        except Exception:
            continue
        if not isinstance(p, dict):
            continue
        if "content" in p:
            out.append(p)                       # 单条笔记快照
        sub = p.get("notes")
        if isinstance(sub, (list, tuple)):
            out.extend(x for x in sub if isinstance(x, dict))   # 文件夹子树快照
    return out


def gc_orphans(notes, base=None, dry_run=False, keep=()):
    """回收 media/ 下没有任何笔记引用的 PNG。

    删除笔记 / 图床上传成功后调用；回收站快照里的笔记也算引用方（调用方把
    回收站 payload 里的 notes 一起传进来），否则「删了还能还原」就断了图。
    """
    d = media_dir(base)
    removed = []
    try:
        names = [x for x in os.listdir(d) if _NAME_RE.match(x)]
    except OSError:
        return removed
    if not names:
        # 常见情况（全库没图 / 图已删净）：media 无合法 PNG，直接返回，
        # 省掉 collect_refs 遍历全库 content —— _flush_save 每次落库都会走到这。
        return removed
    refs = collect_refs(notes) | set(str(x) for x in (keep or ()))
    for name in names:
        if not _NAME_RE.match(name):
            continue
        src = MEDIA_DIR_NAME + "/" + name
        if src in refs:
            continue
        p = os.path.join(d, name)
        if not dry_run:
            try:
                os.remove(p)
            except OSError:
                continue
        removed.append(src)
    return removed


# ---------- Markdown 语法：整行 ![alt](src) ----------
# 尖括号写法 <path with space> 里允许空格，普通写法不允许（防把后面的正文吃进来）
_MD_IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\((<[^>]*>|\S+?)\)\s*$")


def parse_md_image(line):
    """整行图片语法 → (alt, src)；不是整行图片语法就返回 None。

    只认整行是刻意的：行内 `前文 ![x](y) 后文` 要在 Text 里塞行内窗口，
    和标题/引用/待办的行级标签抢位置，复杂度高一个数量级。用户已拍板首版
    只做整行。src 允许带尖括号 `<path with space>`（Markdown 的标准写法）。
    """
    if not isinstance(line, str):
        return None
    m = _MD_IMAGE_RE.match(line)
    if not m:
        return None
    alt, src = m.group(1), m.group(2).strip()
    if len(src) >= 2 and src[0] == "<" and src[-1] == ">":
        src = src[1:-1]
    return (alt, src) if src else None


def parse_md_image_input(text, old_alt=""):
    """「图片地址」输入框的内容 → (alt, src)；空输入 / 只有半截语法返回 None。

    两种写法都收：

      * 整行 Markdown ``![alt](src)`` —— 对话框里预填的就是这个，用户改地址即可；
      * 裸地址 / 路径 —— 老习惯，alt 沿用原来的。

    只写了 ``![]()`` 这种没地址的残句要判空，别把 ``![]()`` 四个字符当路径
    丢给下载器（那会得到一句莫名其妙的「文件不存在」）。
    """
    s = str(text or "").strip()
    if not s:
        return None
    got = parse_md_image(s)
    if got is not None:
        return got
    if s.startswith("!["):
        return None                 # 写了 Markdown 但地址是空的
    if len(s) >= 2 and s[0] == "<" and s[-1] == ">":
        s = s[1:-1].strip()
    return (str(old_alt or ""), s) if s else None


def format_md_image(block):
    """img 数据块 → Markdown 整行文本。导出用。"""
    try:
        d = block[1] if isinstance(block[1], dict) else {}
    except Exception:
        return ""
    alt = str(d.get("alt") or "").replace("]", "\\]")
    src = str(d.get("src") or "")
    if not src:
        return ""
    if re.search(r"\s", src) and not is_remote(src):
        src = "<%s>" % src          # 带空格的路径按 Markdown 规矩加尖括号
    return "![%s](%s)" % (alt, src)
