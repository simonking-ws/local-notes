# -*- coding: utf-8 -*-
"""预渲染图标的测试：每一档位图是否齐全、尺寸对不对、.ico 是否完整。

为什么值得单独测：图标清晰度是靠「运行时零缩放」保证的 —— 一旦某个档位的
`icon/px/<size>/<name>` 漏生成，代码会**静默**退回原图缩放（还是能跑，只是又糊
了）。肉眼很难发现「顶栏某枚图标今晚比昨天软一点」，所以在测试里把「每个用到的
尺寸都必须存在且尺寸精确」写成断言。

不依赖 Pillow：PNG 只读文件头里的 IHDR，ICO 只读 ICONDIR，标准库 `struct` 足够。

    python tests\\test_icons.py
或：
    python -m unittest discover -s tests -v
"""
import os
import struct
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "icon")
PX = os.path.join(ICON, "px")

# 程序里实际会请求的图标与档位。生成脚本还会顺手出一些 2 倍图，这里只钉住
# 「用得上」的那几档 —— 漏了就是真糊，多出来的没关系。
REQUIRED = {
    "app_icon.png": (16, 44, 64),      # 顶栏 / 关于弹窗 / 窗口图标兜底
    "db.png": (16,),                   # 顶栏「数据管理」
    "about.png": (16,),                # 顶栏「关于」
    "create.png": (16,),               # 顶栏「新建笔记」
    "note.png": (12,),                 # 标签页左侧的斜铅笔（按高度取）
    "code.png": (16,),                 # 工具栏「代码块」的花括号
    "github.png": (16,),               # 关于弹窗「项目地址」
    "issue.png": (16,),                # 关于弹窗「问题反馈」
    "notice.png": (16,),               # 正文里的任务提醒
}

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# 工具栏三枚列表图标。它们由 .workbuddy/shot/gen_list_icon_px.py 参数化重绘
# （不进 gen_icons.plan()），所以单独钉住：档位齐全、墨迹框与同排图标同量级、
# 且不能退回「缩放出来的一圈半透明灰边」。
LIST_ICONS = ("unorder_list.png", "list.png", "arrow.png")
LIST_SIZES = (12, 16, 20, 24, 28, 32, 36, 40, 48, 56, 64)

# Windows 的缩放档位。运行时请求的物理尺寸是 round(逻辑尺寸 × 缩放档)，
# 所以每个「逻辑尺寸 × 档位」都要有精确尺寸的成品 —— 少一档就会静默退回
# 原图重采样，高 DPI 下又变糊。档位列表必须和 target/gen_icons.py 一致。
DPI_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)


def dpi_size(logical, scale):
    """和 scripts/dpi.py 的 px() 用同一套四舍五入，避免两边各算一套。"""
    return int(logical * scale + 0.5)


def png_size(path):
    """读 PNG 的 IHDR，返回 (宽, 高)。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("不是 PNG：%s" % path)
    return struct.unpack(">II", head[16:24])


def png_ink_pixels(path, min_alpha=40):
    """数一数有多少像素「有墨」（alpha ≥ min_alpha）。

    标准库解一遍 PNG（本项目产物固定是 8bit RGBA，所以只处理 color type 6）：
    先解 IDAT，再按 PNG 的五种过滤器还原扫描线。够用的最小实现，几十毫秒。
    """
    with open(path, "rb") as f:
        data = f.read()
    pos, idat = 8, b""
    w = h = bd = ct = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", payload[:10])
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
        pos += 12 + length
    if (bd, ct) != (8, 6):
        raise AssertionError("产物应统一为 8bit RGBA：%s" % path)
    raw = zlib.decompress(idat)
    stride, bpp = w * 4, 4
    prev = bytearray(stride)
    ink, p = 0, 0
    for _y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif ft != 0:
            raise AssertionError("未知 PNG 过滤器 %d：%s" % (ft, path))
        for i in range(3, stride, 4):
            if line[i] >= min_alpha:
                ink += 1
        prev = line
    return ink


def ico_sizes(path):
    """读 ICO 的目录，返回各档边长（0 表示 256）。"""
    with open(path, "rb") as f:
        data = f.read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if (reserved, kind) != (0, 1):
        raise AssertionError("不是 ICO：%s" % path)
    out = []
    for i in range(count):
        w, h = struct.unpack("<BB", data[6 + i * 16:8 + i * 16])
        out.append((w or 256, h or 256))
    return out


class TestIcons(unittest.TestCase):
    def test_every_required_size_exists(self):
        missing = []
        for name, sizes in REQUIRED.items():
            for size in sizes:
                p = os.path.join(PX, str(size), name)
                if not os.path.exists(p):
                    missing.append(os.path.relpath(p, ROOT))
        self.assertEqual(missing, [], "缺图标产物，跑 python target/gen_icons.py：%s"
                         % ", ".join(missing))

    def test_every_dpi_scale_is_covered(self):
        """每个逻辑尺寸 × 每个 Windows 缩放档，都要有精确尺寸的成品。

        这一条是「高 DPI 不糊」的真正保险：漏一档不会报错，只会走回退路径
        （原图重采样），肉眼要拿两台机器对比才看得出来。
        """
        missing = []
        for name, sizes in REQUIRED.items():
            for logical in sizes:
                for scale in DPI_SCALES:
                    size = dpi_size(logical, scale)
                    p = os.path.join(PX, str(size), name)
                    if not os.path.exists(p):
                        missing.append("%s@%sx → %s" % (name, scale,
                                                        os.path.relpath(p, ROOT)))
        self.assertEqual(missing, [],
                         "这些「图标 × 缩放档」缺产物，跑 python target/gen_icons.py：\n  "
                         + "\n  ".join(missing))

    def test_generator_agrees_with_dpi_layer(self):
        """生成脚本的档位表必须和本测试一致 —— 两边各写一份，漂了就会悄悄缺图。"""
        import sys
        target = os.path.join(ROOT, "target")
        sys.path.insert(0, target)
        try:
            import gen_icons
        finally:
            sys.path.remove(target)
        self.assertEqual(tuple(gen_icons.DPI_SCALES), tuple(DPI_SCALES))
        # 生成脚本按逻辑尺寸展开，所以 plan() 里的边长集合必须覆盖这里算出来的
        generated = {size for (_p, _s, size, _m) in gen_icons.plan()}
        wanted = {dpi_size(logical, scale)
                  for sizes in REQUIRED.values() for logical in sizes
                  for scale in DPI_SCALES}
        self.assertEqual(wanted - generated, set(),
                         "gen_icons.plan() 少算了这些档位")

    def test_png_dimensions_are_exact(self):
        """文件边长必须等于目录名 —— 差 1px 就说明有人手改了图，运行时会对不齐。"""
        bad = []
        for name, sizes in REQUIRED.items():
            for size in sizes:
                p = os.path.join(PX, str(size), name)
                if not os.path.exists(p):
                    continue
                w, h = png_size(p)
                if name == "note.png":
                    # 横图：只按高度对齐，宽度随比例（12 高 → 12 宽）
                    ok = h == size and w <= size
                else:
                    ok = (w, h) == (size, size)
                if not ok:
                    bad.append("%s: %dx%d != %d" % (os.path.relpath(p, ROOT), w, h, size))
        self.assertEqual(bad, [], "尺寸不符：" + "; ".join(bad))

    def test_icon_is_not_blank(self):
        """产物不能是「全透明」—— 生成脚本出 bug 时最容易得到一张空图。"""
        empty = []
        for name, sizes in REQUIRED.items():
            for size in sizes:
                p = os.path.join(PX, str(size), name)
                if not os.path.exists(p):
                    continue
                # 12px 的铅笔也有几十个着墨像素；门槛取 8 只是为了抓「整张空掉」
                if png_ink_pixels(p) < 8:
                    empty.append(os.path.relpath(p, ROOT))
        self.assertEqual(empty, [], "图标疑似空白：" + ", ".join(empty))

    def test_list_icons_have_every_size(self):
        """三枚列表图标 11 档齐全且尺寸精确：漏一档就静默回退原图重采样。"""
        bad = []
        for name in LIST_ICONS:
            for size in LIST_SIZES:
                p = os.path.join(PX, str(size), name)
                if not os.path.exists(p):
                    bad.append(os.path.relpath(p, ROOT))
                elif png_size(p) != (size, size):
                    bad.append("%s 尺寸 %s" % (os.path.relpath(p, ROOT), png_size(p)))
        self.assertEqual(bad, [], "三枚列表图标缺档 / 尺寸不对：%s" % ", ".join(bad))

    def test_list_icons_are_crisp(self):
        """纯整数像素网格重绘：alpha 只该有 0 / 255 两档，不该有半透明灰雾边。

        旧版把 215×200 源图 LANCZOS 缩到 16px，边缘摊成半透明 → 「看着太浅」。
        这里用 alpha≥1 与 alpha≥128 的像素数之差来抓：正常应 ≤2 个像素。
        """
        bad = []
        for name in LIST_ICONS:
            p = os.path.join(PX, "16", name)
            if not os.path.exists(p):
                continue
            soft = png_ink_pixels(p, min_alpha=1)
            hard = png_ink_pixels(p, min_alpha=128)
            if hard < 55:
                bad.append("%s 墨量不足（%d px）" % (name, hard))
            if soft - hard > 2:
                bad.append("%s 有 %d 个半透明像素" % (name, soft - hard))
        self.assertEqual(bad, [], "列表图标又糊了：%s" % "; ".join(bad))

    def test_list_icons_match_neighbour_size(self):
        """墨迹框与同排 code.png 同量级，别比邻居胖一圈或瘦一圈。"""
        ref = os.path.join(PX, "16", "code.png")
        if not os.path.exists(ref):
            self.skipTest("code.png 还没生成")
        n_ref = png_ink_pixels(ref, min_alpha=128)
        bad = []
        for name in LIST_ICONS:
            p = os.path.join(PX, "16", name)
            if not os.path.exists(p):
                continue
            n = png_ink_pixels(p, min_alpha=128)
            if not (0.60 * n_ref <= n <= 1.20 * n_ref):
                bad.append("%s 墨迹 %d px，参照 %d px" % (name, n, n_ref))
        self.assertEqual(bad, [], "列表图标和同排图标大小不齐：%s" % "; ".join(bad))

    def test_ico_has_all_sizes(self):
        """.ico 是打包产物（.gitignore 里），没有就跳过；有就必须七档齐全。"""
        p = os.path.join(ICON, "app_icon.ico")
        if not os.path.exists(p):
            self.skipTest("icon/app_icon.ico 还没生成（打包 / gen_icons 时产出）")
        self.assertEqual(ico_sizes(p), [(s, s) for s in ICO_SIZES])

    def test_sources_still_present(self):
        """源图必须留着：重建 / 换尺寸 / 以后再出 2 倍图全靠它。"""
        for name in REQUIRED:
            self.assertTrue(os.path.exists(os.path.join(ICON, name)),
                            "源图丢了：icon/%s" % name)


if __name__ == "__main__":
    unittest.main()
