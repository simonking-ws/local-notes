# -*- coding: utf-8 -*-
"""把 `icon/` 下的源图预渲染成每一档「精确尺寸」，供运行时零缩放直接取用。

为什么要有这一步（而不是让程序在运行时缩）：

    `icon/db.png` 是 200×200 的线条图，笔画只有 16px 宽。顶栏要的是 16px ——
    200→16 是 12.5 倍，一条线缩完只剩 1.2px，再被 Tk 的最近邻采样丢掉一半，
    就成了断断续续的灰点。缩放这一步**必须发生在有质量控制的地方**（这里，
    用 LANCZOS + 透明度增益），而不是在每次重绘时由 Tk 凑。

产物：

    icon/px/<size>/<name>.png      # 每一档的成品，运行时零缩放直接贴
    icon/app_icon.ico              # 16/24/32/48/64/128/256 七档，每档都是渲染好的

    · 线条图标先按 alpha 包围盒裁掉四周透明边，图形正好铺满目标尺寸；
    · 缩放后对 alpha 做一次「增益」（把被平均掉的墨量补回去），细线才立得住；
    · 需要运行时染色的五张（create / github / issue / note / code）输出成**白色**，
      运行时只换 RGB、保留 alpha，不必再缩放；其中 code.png 没有设计源图，
      形状由 `target/make_brace_icon.py` 按参数化几何画出（Tk 画不了平滑曲线）；
    · `app_icon.png` 在 32px 及以下走「小尺寸优化」：裁边 + 提高饱和度/对比度 +
      锐化 —— 512px 的拟物图直接缩到 16px 只剩一团米色，提饱和后铅笔才认得出；
    · .ico 逐档取自上面的成品，而不是让 Pillow 从 512 再缩一遍。

用法：

    python target/gen_icons.py          # 重新生成全部尺寸
    python target/gen_icons.py --check  # 只检查产物是否齐全（CI / 测试用）

打包时 `build.py` 会自动跑一次，所以 exe 里的图标一定是最新的。
"""
import os
import struct
import sys

TARGET_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TARGET_DIR)
ICON_DIR = os.path.join(ROOT, "icon")
OUT_DIR = os.path.join(ICON_DIR, "px")

# Windows 的缩放档位。每个「100% 下的逻辑尺寸」在这些比例下都出一张成品，
# 运行时 dpi.px() 算出来的物理像素就能精确命中，一个像素都不用重采样。
# 300% 也放进来：4K 屏 + 放大镜场景下确实有人用，多出来的文件很小。
DPI_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)

# 每张图在 100% 下会被请求的尺寸（见 widgets.py / app_ui.py 的调用点）：
#   · 顶栏 / 关于弹窗 / 正文提醒的线条图 → 16
#   · 标签页左侧的斜铅笔 → 12（按高度取）
#   · 应用图标 → 顶栏 16、关于弹窗 44、窗口图标兜底 64
LINE_LOGICAL = {
    "create.png": (16,),
    "github.png": (16,),
    "issue.png":  (16,),
    "note.png":   (12,),
    "code.png":   (16,),
    "db.png":     (16,),
    "about.png":  (16,),
    "notice.png": (16,),
}
APP_LOGICAL = (16, 44, 64)


def dpi_sizes(logical):
    """把一组逻辑尺寸展开成所有缩放档下的物理尺寸（去重、升序）。"""
    out = set()
    for base in logical:
        for k in DPI_SCALES:
            out.add(int(base * k + 0.5))
    return sorted(out)


# 兼容旧的引用（测试 / 文档里会提到 SIZES）
SIZES = dpi_sizes(set().union(*LINE_LOGICAL.values(), APP_LOGICAL))

# name -> (缩放方式, 是否输出白色供运行时染色[, 墨迹占比])
#   "fit"    : 裁 bbox 后等比缩进 size×size 的透明画布（图形铺满、居中）
#   "height" : 只按高度缩放、宽度保持比例（note.png 是 367×200 的横图）
#   第三项可选：墨迹占边长比例，默认 1.0（铺满）。花括号取 0.90，与工具栏里
#   14px 的矢量图标等大 —— 撑满 16px 会比邻居胖一圈。
LINE_ICONS = {
    "create.png": ("fit", True),
    "github.png": ("fit", True),
    "issue.png":  ("fit", True),
    "note.png":   ("height", True),
    "code.png":   ("fit", True, 0.90),
    "db.png":     ("fit", True),
    "about.png":  ("fit", True),
    "notice.png": ("fit", True),
}
APP_ICON = "app_icon.png"

# 小尺寸下给 app_icon 用的「光学修正」参数：越小的图标越需要提对比度，
# 否则 16px 就是一坨米色。数值是拿 16/20/24/32 四档肉眼对出来的。
_APP_SMALL_MAX = 32
_APP_SMALL = dict(crop=0.06, color=1.9, contrast=1.3,
                  sharp_radius=0.6, sharp_percent=100)


def _alpha_gain(im, gain=1.32, floor=14):
    """把缩小时被平均掉的墨量补回去：alpha 走一条轻微上凸的曲线。

    gain=1 时不动。floor 以下的像素直接清零，去掉边缘那圈若有若无的灰雾
    （16px 上这圈灰雾就是「糊」的主要观感来源）。
    """
    a = im.split()[3]
    lut = []
    for v in range(256):
        if v < floor:
            lut.append(0)
        else:
            lut.append(min(255, int(round((v / 255.0) ** 0.86 * 255 * gain))))
    im.putalpha(a.point(lut))
    return im


def _to_white(im):
    """把 RGB 刷成白色、只留 alpha —— 运行时染色时这就是一张「掩膜」。"""
    from PIL import Image
    out = Image.new("RGBA", im.size, (255, 255, 255, 255))
    out.putalpha(im.split()[3])
    return out


def _fit(im, size, mode, fill=1.0):
    """等比缩放并（fit 模式）居中贴进 size×size 的透明画布。

    fill < 1 时墨迹只占边长的一部分（四周留透明边）：有些图标在 UI 里就该比
    格位小一圈（花括号要和同排 14px 的矢量图标等大，撑满 16 会显胖）。
    """
    from PIL import Image
    resample = getattr(Image, "LANCZOS", Image.BICUBIC)
    w, h = im.size
    if mode == "height":
        nw = max(1, int(round(size * w / float(h))))
        return im.resize((nw, size), resample)
    k = min(size / float(w), size / float(h)) * fill
    nw, nh = max(1, int(round(w * k))), max(1, int(round(h * k)))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if fill < 1.0:
        # 留边图标（目前只有 code.png）居中时**向上取整**：墨迹行数常常是奇数、
        # 格位是偶数，(size-nh)/2 会落在 x.5 上。向下取整会让整枚图在高 DPI 下
        # 比同排图标高 1px（150% 实测：花括号 49..69，复选框 50..70）。
        ox = int((size - nw) / 2.0 + 0.5)
        oy = int((size - nh) / 2.0 + 0.5)
    else:
        ox, oy = (size - nw) // 2, (size - nh) // 2
    canvas.paste(im.resize((nw, nh), resample), (ox, oy))
    return canvas


def _render_line(src, size, mode, fill=1.0):
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    bb = im.split()[3].getbbox()
    if bb:
        im = im.crop(bb)                  # 裁掉四周透明边，图形铺满
    im = _fit(im, size, mode, fill)
    return _alpha_gain(im)


def _render_app_icon(src, size):
    from PIL import Image, ImageEnhance, ImageFilter
    im = Image.open(src).convert("RGBA")
    if size > _APP_SMALL_MAX:
        return im.resize((size, size),
                         getattr(Image, "LANCZOS", Image.BICUBIC))
    w, h = im.size
    c = _APP_SMALL["crop"]
    im = im.crop((int(w * c), int(h * c), w - int(w * c), h - int(h * c)))
    im = ImageEnhance.Color(im).enhance(_APP_SMALL["color"])
    im = ImageEnhance.Contrast(im).enhance(_APP_SMALL["contrast"])
    im = im.resize((size, size), getattr(Image, "BOX", Image.BILINEAR))
    return im.filter(ImageFilter.UnsharpMask(
        radius=_APP_SMALL["sharp_radius"],
        percent=_APP_SMALL["sharp_percent"], threshold=0))


def plan():
    """(输出路径, 源路径, size, mode) 的完整清单，--check 和测试共用。"""
    jobs = []
    for name, spec in LINE_ICONS.items():
        mode = spec[0]
        for size in dpi_sizes(LINE_LOGICAL.get(name, (16,))):
            jobs.append((os.path.join(OUT_DIR, str(size), name),
                         os.path.join(ICON_DIR, name), size, mode))
    for size in dpi_sizes(APP_LOGICAL):
        jobs.append((os.path.join(OUT_DIR, str(size), APP_ICON),
                     os.path.join(ICON_DIR, APP_ICON), size, "app"))
    return jobs


# .ico 里要放的档位。16/24/32/48 用上面「小尺寸优化」过的那几张，64 以上用原图的
# LANCZOS —— 任务栏和文件属性页在 100% 缩放下要的是 32，高 DPI 才会去拿 48/64。
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _dib(im):
    """把 RGBA 图打包成 ICO 条目用的 BMP：BITMAPINFOHEADER + BGRA（自下而上）+ AND 掩膜。

    不用 Pillow 的 `save(sizes=[...])`：那里所有档位都是从**同一张图**缩出来的，
    16px 会退化成 512 拟物图的又一次缩放 —— 正是要避免的事。这里每一档都是
    已经渲染好的成品，原样塞进容器。
    """
    w, h = im.size
    px = im.tobytes()                       # RGBA, 自上而下
    rows = []
    for y in range(h - 1, -1, -1):          # BMP 是自下而上
        row = px[y * w * 4:(y + 1) * w * 4]
        out = bytearray(w * 4)
        for x in range(w):
            r, g, b, a = row[x * 4:x * 4 + 4]
            out[x * 4:x * 4 + 4] = bytes((b, g, r, a))
        rows.append(bytes(out))
    xor = b"".join(rows)
    # 32 位图的 AND 掩膜不参与显示（alpha 说了算），但格式要求在，全 0 即可
    and_row = ((w + 31) // 32) * 4
    and_mask = b"\x00" * (and_row * h)
    header = struct.pack("<IiiHHIIiiII",
                         40, w, h * 2, 1, 32, 0, len(xor), 0, 0, 0, 0)
    return header + xor + and_mask


def _ico_frame(size):
    """优先取预渲染的成品；128/256 这种只给 .ico 用的档位就现场从原图缩。"""
    from PIL import Image
    path = os.path.join(OUT_DIR, str(size), APP_ICON)
    if os.path.exists(path):
        return Image.open(path).convert("RGBA")
    im = Image.open(os.path.join(ICON_DIR, APP_ICON)).convert("RGBA")
    return im.resize((size, size), getattr(Image, "LANCZOS", Image.BICUBIC))


def build_ico(out=None):
    """按 ICO_SIZES 组装 icon/app_icon.ico，返回写出的路径。"""
    out = out or os.path.join(ICON_DIR, "app_icon.ico")
    frames = [_ico_frame(s) for s in ICO_SIZES]
    blobs = [_dib(im) for im in frames]
    offset = 6 + 16 * len(frames)
    entries = b""
    for im, blob in zip(frames, blobs):
        w, h = im.size
        entries += struct.pack("<BBBBHHII",
                               w if w < 256 else 0, h if h < 256 else 0,
                               0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(out, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(frames)) + entries + b"".join(blobs))
    return out


def generate(verbose=True):
    """生成全部产物，返回写出的文件数。"""
    if not os.path.isdir(ICON_DIR):
        raise SystemExit("找不到 icon 目录：%s" % ICON_DIR)
    n = 0
    for out, src, size, mode in plan():
        if not os.path.exists(src):
            raise SystemExit("源图不存在：%s" % src)
        if mode == "app":
            im = _render_app_icon(src, size)
        else:
            spec = LINE_ICONS[os.path.basename(src)]
            fill = spec[2] if len(spec) > 2 else 1.0
            im = _render_line(src, size, mode, fill)
            if spec[1]:
                im = _to_white(im)
        d = os.path.dirname(out)
        if not os.path.isdir(d):
            os.makedirs(d)
        im.save(out, optimize=True)
        n += 1
        if verbose:
            print("  %-42s %s" % (os.path.relpath(out, ROOT), im.size))
    ico = build_ico()
    if verbose:
        print("  %-42s %d 档" % (os.path.relpath(ico, ROOT), len(ICO_SIZES)))
    return n


def check():
    """产物是否齐全（不比对像素，只比对文件在不在）。返回缺失清单。"""
    missing = [out for out, _src, _size, _mode in plan()
               if not os.path.exists(out)]
    ico = os.path.join(ICON_DIR, "app_icon.ico")
    if not os.path.exists(ico):
        missing.append(ico)
    return missing


def main(argv):
    if "--check" in argv:
        missing = check()
        if missing:
            print("缺少 %d 个图标产物，先跑 python target/gen_icons.py：" % len(missing))
            for p in missing[:20]:
                print("  " + os.path.relpath(p, ROOT))
            return 1
        print("图标产物齐全（%d 个）" % len(plan()))
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("生成图标需要 Pillow：python -m pip install pillow")
        return 1
    print("生成图标 → %s" % os.path.relpath(OUT_DIR, ROOT))
    n = generate()
    print("完成，共 %d 张位图 + 1 个 .ico。" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
