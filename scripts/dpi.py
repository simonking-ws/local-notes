# -*- coding: utf-8 -*-
"""DPI 感知与全局像素缩放 —— 让界面在 125% / 150% / 200% 下仍然清晰。

为什么需要这一层：Tk 的 `tk scaling` 只影响**点值字体**，所有写死的像素常量
（顶栏高度、图标画布、padx/pady、画布坐标）不会跟着 Windows 缩放比变。如果不做
这两件事，高 DPI 机器上会遇到两种坏结果之一：

    · 不声明 DPI 感知 → 窗口按 100% 画，Windows 再把整张窗口位图拉伸 1.25 倍，
      文字和图标一起发虚。这一层模糊发生在 DWM，任何图标资源都救不了。
    · 只声明感知、不缩放常量 → 界面只剩原来的 2/3 大，字变大、容器没变，一片拥挤。

所以这里同时做两件事：

    1. 在创建任何窗口之前声明「系统级 DPI 感知」（system DPI aware）；
    2. 给出 SCALE 与 `px()`，让 widgets / app_ui 在**导入期**就把像素常量换成
       物理像素。

⚠️ 调用顺序是硬约束：`dpi.init()` 必须早于 `from widgets import ...`。
app_ui 里有 `from widgets import TOP_BAR_H` 这样的名字拷贝，等 Tk 建好再改
SCALE 已经晚了 —— 那些变量拿到的还是旧值。见 notes_app.py 顶部的用法。

没有声明 per-monitor 感知：那需要在窗口被拖到另一块屏时接收 WM_DPICHANGED 并把
整棵控件树按新比例重建，对 Tk 这种「一套比例画到底」的界面改动量不成比例。
system aware 已经足够：程序按主屏比例渲染，跨屏时系统只做一次位图缩放（副屏才
会看到，主屏始终锐利）。
"""
import os

# 当前缩放比：1.0 = 100%。所有 `px()` 都乘它。
# 默认 1.0 是为了让测试和「没有调用 init 的脚本」保持 100% 的确定性 ——
# 测试断言的是设计稿尺寸，不该随开发机的 DPI 漂移。
SCALE = 1.0


def enable_awareness():
    """声明 DPI 感知。必须在任何 Tk 窗口（含隐式默认 root）之前调用。

    优先 `shcore.SetProcessDpiAwareness(1)`（Win8.1+ 的正式 API；1 = system DPI
    aware），老系统退回 `user32.SetProcessDPIAware()`。失败不抛：最坏的结果是
    退回「系统帮我们拉伸」，界面仍可用，只是不锐。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def _system_dpi():
    """当前系统 DPI（96 为 100%）。只用 ctypes，不依赖 Tk 是否已建好。"""
    if os.name != "nt":
        return 96
    try:
        import ctypes
        dpi = int(ctypes.windll.user32.GetDpiForSystem())    # Win10 1607+
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        # 老系统：拿屏幕 DC 问 LOGPIXELSY。必须在 SetProcessDPIAware 之后才准。
        import ctypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        try:
            dpi = int(gdi32.GetDeviceCaps(hdc, 88))          # LOGPIXELSY
        finally:
            user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


def init(scale=None, awareness=True):
    """确定本次运行的缩放比并返回它（重复调用是幂等的）。

    scale 为 None 时按 `SUJIAN_UI_SCALE` 环境变量 → 系统 DPI 的顺序取值。
    环境变量是给验证用的：`set SUJIAN_UI_SCALE=1.5 && python scripts\\notes_app.py`
    可以在 100% 的机器上模拟 150%，不必改系统设置。
    """
    global SCALE
    if awareness:
        enable_awareness()
    if scale is None:
        raw = (os.environ.get("SUJIAN_UI_SCALE") or "").strip()
        if raw:
            try:
                scale = float(raw)
            except ValueError:
                scale = None
    if scale is None:
        scale = _system_dpi() / 96.0
    # 夹到 [0.75, 4.0]：手改环境变量或系统 DPI 异常时，界面不会缩成一条缝或撑爆屏幕
    SCALE = min(4.0, max(0.75, float(scale)))
    return SCALE


def px(n):
    """设计稿像素 → 当前缩放下的物理像素（四舍五入到整数）。

    一定要落回整数：Tk 画布上 0.5px 的偏移会把 1px 的线摊成两条半透明灰线，
    细描边、图标外框最先遭殃 —— 那正是「糊」的另一半来源。
    """
    if not n:
        return 0
    n = float(n)
    return int(n * SCALE + (0.5 if n > 0 else -0.5))


def fx(n):
    """同 `px()`，但保留小数 —— 给线宽这类「不一定要对齐到整像素」的量用。"""
    return float(n) * SCALE


def apply_tk_scaling(root):
    """把 Tk 的「点 → 像素」换算对齐到 SCALE。

    Tk 默认按系统 DPI 算 scaling（100% 时是 96/72）：字体写 `size=13`（点），
    实际像素 = 13 × scaling。我们声明了 DPI 感知，这一步本来也会算对；显式设一次
    有两个好处：① 用 `SUJIAN_UI_SCALE` 模拟高 DPI 时字也跟着放大，截出来的图才
    是真实比例；② 不受 Tk 版本 / 主题对 DPI 读取差异的影响。

    必须在**创建控件之前**调用 —— 字体对象在控件创建时就把像素高度定死了。
    """
    try:
        root.tk.call("tk", "scaling", SCALE * 96.0 / 72.0)
    except Exception:
        pass


def scale_canvas(canvas):
    """把画布上**已按 1× 逻辑坐标画好**的图元整体放大到当前 SCALE。

    只给那些「坐标写死在 18×16 这类逻辑格里、与画布实际大小无关」的小图标用
    （文件夹图标）。其它控件都是从已缩放的常量推坐标，再调这个会放大两次。
    Tk 的 `Canvas.scale` 只改坐标、不改线宽，所以描边宽度要另外补乘。
    """
    if SCALE == 1.0:
        return
    try:
        canvas.scale("all", 0, 0, SCALE, SCALE)
        for item in canvas.find_all():
            try:
                w = float(canvas.itemcget(item, "width"))
            except Exception:
                continue
            if w:
                canvas.itemconfigure(item, width=w * SCALE)
    except Exception:
        pass
