# -*- coding: utf-8 -*-
"""生成 `icon/code.png` —— 工具栏「代码块」那对花括号的**源图**（白色掩膜）。

为什么这张源图是脚本画出来的，而别的图标是设计稿：
花括号是一个纯几何字形，没有细节可画，只有比例和粗细要调。把它写成参数，
形状就能随 UI 规范一起演进（想再圆一点只改一个数），也不用在仓库里放一张
「来历不明、改不动」的位图。

为什么不在运行时用 Canvas 画（原来就是这么做的）：
Tk 8.6 的 canvas 线条**没有抗锯齿**。实测 16px 的花括号整枚只有 2 个灰阶
（笔画色 / 底色），斜线和腰部全是台阶 —— 这就是「不够圆滑」的来源。
先把形状按 4 倍超采样画出来，再用 LANCZOS 缩到 16px，能得到 ~40 个中间灰阶，
边缘才是平滑的。缩放这一步只在这里做一次，运行时零缩放。

产物：icon/code.png（512×512 RGBA，RGB 全白、信息都在 alpha 里），
之后由 `target/gen_icons.py` 按各 DPI 档位预渲染到 icon/px/<size>/code.png。

    python target/make_brace_icon.py

调参入口在下面的 `PARAMS` / `STROKE`：坐标都是 0~1 的逻辑方格（1 = 16px 的
边长），改完重跑本脚本 + `python target/gen_icons.py` 即可看到成品。
"""
import os

TARGET_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TARGET_DIR)
OUT = os.path.join(ROOT, "icon", "code.png")

# 画布边长（源图分辨率）与超采样倍数。512 足够：最大成品是 48px（300% 档），
# 512 → 48 是 10.7 倍下采样，边缘灰阶够用；再大只是白占体积。
SIZE = 512
SS = 4

# 墨迹占成品的比例。花括号在 16px 工具栏里**不该撑满整格**：同排的矢量图标
# （todo 14px、star 16px）都留了边，撑满会显得比邻居大一圈。0.90 → 墨迹约
# 14.4px，与它们同档。gen_icons 的 `_fit(fill=...)` 用同一个数。
FILL = 0.90

# 右半括号的几何（单位坐标，左半镜像）：
#   spine      竖脊离画布中线的距离（越大，两个括号离得越远）
#   arm        上/下横臂伸向内侧的长度
#   bulge      腰尖向外凸出的长度
#   arm_y      横臂所在的 y（上下对称）
#   corner     横臂转进竖脊的圆角半径
#   waist_drop 竖脊在腰尖之前还直着走多远，之后才开始外凸
#
# 数值沿用原来矢量版的锚点（臂 0.13 / 腰 0.16 / 脊 0.25），只把折角换成曲线：
# 用户报的是「不够圆滑」而不是「形状不对」，比例就别一起动 —— 比例一动，
# 就得重新和同排图标比大小。
PARAMS = dict(spine=0.250, arm=0.130, bulge=0.160, arm_y=0.110,
              corner=0.045, waist_drop=0.130)

# 笔画粗细（**成品** 16px 下的像素）：竖粗横细是花括号在小字号下能认出来的
# 关键 —— 等宽描边在 16px 上就是两根等粗的柱子加一个疙瘩。数值比矢量版的
# 1.7 略粗，因为抗锯齿会把边缘摊成半透明，视觉重量会掉一点。
STROKE = dict(spine=1.92, arm=1.74, waist=1.86)


def _quad(p0, p1, p2, n=24):
    out = []
    for i in range(n + 1):
        t = i / float(n)
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _bez(p0, p1, p2, p3, n=48):
    out = []
    for i in range(n + 1):
        t = i / float(n)
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _seg(p0, p1, n=60):
    return [(p0[0] + (p1[0] - p0[0]) * i / float(n),
             p0[1] + (p1[1] - p0[1]) * i / float(n)) for i in range(n + 1)]


def _half(sign):
    """右半括号（sign=1）或左半（sign=-1）的中心线，从上臂尖走到腰尖。"""
    p = PARAMS
    sx = 0.5 + sign * p["spine"]
    tx = 0.5 + sign * (p["spine"] - p["arm"])
    bx = 0.5 + sign * (p["spine"] + p["bulge"])
    ay, cy = p["arm_y"], 0.5
    pts = _quad((tx, ay), (sx, ay), (sx, ay + p["corner"]))
    pts += _seg((sx, ay + p["corner"]), (sx, cy - p["waist_drop"]))
    pts += _bez((sx, cy - p["waist_drop"]), (sx, cy - 0.04),
                (bx, cy - 0.035), (bx, cy))[1:]
    return pts


def _widths(n):
    """沿路径的宽度剖面：横臂最细 → 竖脊最粗 → 腰尖略收。"""
    out = []
    for i in range(n):
        t = i / float(n - 1)
        if t < 0.34:
            k = (t / 0.34) ** 0.6
            w = STROKE["arm"] + (STROKE["spine"] - STROKE["arm"]) * k
        elif t < 0.74:
            w = STROKE["spine"]
        else:
            w = STROKE["spine"] + (STROKE["waist"] - STROKE["spine"]) * ((t - 0.74) / 0.26)
        out.append(w)
    return out


def render():
    """按「画笔刷」的方式上墨：沿路径铺圆点，直径随剖面变化（圆角自然出现）。"""
    from PIL import Image, ImageDraw

    W = SIZE * SS
    # 1 个「成品 16px 文件里的像素」= k 个超采样像素。墨迹在 W 上铺满，
    # gen_icons 会把它 fit 到 16*FILL —— 两边用同一个 FILL，成品里才有留白。
    k = W / (16.0 * FILL)
    big = Image.new("L", (W, W), 0)
    d = ImageDraw.Draw(big)
    for sign in (1, -1):
        top = _half(sign)
        bottom = [(x, 1.0 - y) for x, y in reversed(top[:-1])]
        pts = [(x * W, y * W) for x, y in top + bottom]
        for (x, y), w in zip(pts, _widths(len(pts))):
            r = w * k / 2.0
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    ink = big.resize((SIZE, SIZE), Image.LANCZOS)

    out = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 0))
    out.putalpha(ink)
    return out


def main():
    im = render()
    bb = im.split()[3].getbbox()
    d = os.path.dirname(OUT)
    if not os.path.isdir(d):
        os.makedirs(d)
    im.save(OUT, optimize=True)
    print("写出 %s  %dx%d  墨迹 %dx%d" % (
        os.path.relpath(OUT, ROOT), im.size[0], im.size[1],
        bb[2] - bb[0], bb[3] - bb[1]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
