# -*- coding: utf-8 -*-
"""自绘窗口框架：让「素笺」的顶栏真正充当标题栏。

Windows 的窗口标题栏属于 non-client area（非客户区），由系统连同阴影、
三枚系统按钮一起绘制，Tk 没法往里塞控件。这里的解法不是去掉窗口边框
（那会连阴影、贴边吸附、任务栏按钮、最小化动画一起丢掉），而是**改报客户区**：

拦下 WM_NCCALCSIZE，把系统算好的客户区从「标题栏之下」抬到「窗口最上沿」，
左右和底部仍按系统边框内缩，好让 DWM 的阴影有落脚处。

于是系统不再画标题栏，而窗口样式原封未动 —— 阴影 / 圆角 / 任务栏按钮 /
Alt+Tab / 边缘拖拽缩放都还是系统那一份。换来的代价只有三条：

  * 右上角三枚系统按钮没了  → widgets.WindowButton 自己画；
  * 标题栏不能再拖窗口      → 这里绑 Tk 事件自己实现；
  * 双击标题栏不再最大化    → 同上。

⚠️ 两个致命的时序坑（都踩过，别改回去）：

1. WM_NCCALCSIZE 只在窗口创建 / 框架变化时发一次，而窗口创建那会儿还是
   1×1 像素。此时按「窗口矩形」改客户区，会把整窗改成 1×1 的白板。
   所以 install() 必须在窗口**已经有真实尺寸**之后才调用。
2. 挂完窗口过程还得补一发 SWP_FRAMECHANGED，否则系统不会重算客户区，
   标题栏照旧挂在那儿。
"""

import ctypes
import sys

WM_NCCALCSIZE = 0x0083
WM_SIZE = 0x0005
GWLP_WNDPROC = -4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0004, 0x0020
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9
MONITOR_DEFAULTTONEAREST = 2

_IS_WIN = sys.platform.startswith("win")
_u32 = None
_WNDPROC = None
_RECT = None
_NCCALCSIZE_PARAMS = None
_MONITORINFO = None

if _IS_WIN:
    from ctypes import wintypes

    _u32 = ctypes.windll.user32
    _LONG_PTR = (ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8
                 else ctypes.c_long)
    _WNDPROC = ctypes.WINFUNCTYPE(_LONG_PTR, wintypes.HWND, ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.c_void_p)

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                    ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

    _u32.GetParent.restype = wintypes.HWND
    _u32.GetParent.argtypes = [wintypes.HWND]
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _u32.IsZoomed.restype = wintypes.BOOL
    _u32.IsZoomed.argtypes = [wintypes.HWND]
    _u32.MonitorFromWindow.restype = wintypes.HMONITOR
    _u32.MonitorFromWindow.argtypes = [wintypes.HWND, ctypes.c_ulong]
    _u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _u32.CallWindowProcW.restype = ctypes.c_void_p
    _u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                     ctypes.c_void_p, ctypes.c_void_p]
    _u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]


def _parent_hwnd(win):
    """Tk 的顶层窗口套着一层 wrapper，标题栏归 wrapper 管（winfo_id 是里面那层）。"""
    try:
        return _u32.GetParent(win.winfo_id()) or win.winfo_id()
    except Exception:
        return win.winfo_id()


def work_area(win):
    """窗口所在显示器的工作区（扣掉任务栏），返回 (x, y, w, h)。"""
    if not _IS_WIN:
        return (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
    try:
        mon = _u32.MonitorFromWindow(_parent_hwnd(win), MONITOR_DEFAULTTONEAREST)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        _u32.GetMonitorInfoW(mon, ctypes.byref(mi))
        r = mi.rcWork
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())


class WindowFrame(object):
    """把主窗口的系统标题栏换成应用自己的顶栏。

    非 Windows 上 install() 直接返回 False，调用方保留系统标题栏即可 ——
    界面上那一排窗口键也不需要摆出来（可以按 `enabled` 判断）。
    """

    def __init__(self, root):
        self.root = root
        self.enabled = False
        self._hwnd = None
        self._old = None
        self._proc = None
        self._drag = None
        self._icon_pos = None
        self._zoomed = None
        self._on_zoom = None
        self._restore_box = None
        self._maxed = False
        self._inset = None
        # 最近一次「请求摆到哪儿」（客户区原点）。拖动时拿它去重，比读 winfo_x
        # 可靠：窗口消息还没被 Tk 处理完时 winfo 是旧值，会误判成「没动」。
        self._last_pos = None
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self.root.bind("<Map>", self._on_map, add="+")

    def set_on_zoom(self, callback):
        """窗口被最大化 / 还原时回调 callback(is_maximized)。

        用 WM_SIZE 而不是 Tk 的 <Configure>：Win+↑、拖到屏幕边缘吸附、双击
        这些系统行为同样会改变状态，只有窗口消息能一网打尽。
        """
        self._on_zoom = callback

    def set_restore_box(self, x, y, w, h):
        """指定「还原」时该回到哪块位置（客户区坐标，与 wm geometry 同一套语义）。

        为什么要自己记：我们改写了客户区，Tk 记的 normal geometry 是系统报给它的
        客户区尺寸，两者对不上 —— 直接 `state("normal")` 会让窗口还原成一整屏。
        """
        self._restore_box = (int(x), int(y), int(w), int(h))

    # ---------------------------------------------------------------- 装配
    def install(self):
        """挂上窗口过程并强制重算客户区。必须在窗口已有真实尺寸之后调用。"""
        if not _IS_WIN or self.enabled:
            return self.enabled
        try:
            self._hwnd = _parent_hwnd(self.root)
            self._proc = _WNDPROC(self._wndproc)
            self._old = _u32.SetWindowLongPtrW(
                self._hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        except Exception:
            return False
        if not self._old:
            return False
        self.enabled = True
        self._recalc()
        return True

    def uninstall(self):
        """窗口销毁前把窗口过程还回去：Python 回调对象一旦被回收，
        系统再调到它就会直接崩，不能只靠 destroy 顺手收尾。"""
        if not self.enabled or not self._old:
            self.enabled = False
            return
        try:
            _u32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC,
                                   ctypes.cast(self._old, ctypes.c_void_p))
        except Exception:
            pass
        self.enabled = False
        self._old = None

    def _recalc(self):
        try:
            _u32.SetWindowPos(self._hwnd, None, 0, 0, 0, 0,
                              SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            pass

    def _on_destroy(self, event):
        if event.widget is self.root:
            self.uninstall()

    # ------------------------------------------------------------ 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCCALCSIZE and wparam:
                return self._nccalcsize(hwnd, wparam, lparam)
            res = _u32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)
            if msg == WM_SIZE and self._on_zoom:
                # 不能在窗口过程里直接跑 Tk 代码（消息还在系统栈上），排到空闲时再说
                try:
                    self.root.after_idle(self._sync_zoom)
                except Exception:
                    pass
            return 0 if res is None else int(res)
        except Exception:
            # 回调里抛异常会把消息处理卡死，一律吞掉并按「不处理」返回
            return 0

    def _sync_zoom(self):
        """窗口尺寸变了：同步一次最大化状态（Win+↑、拖到屏幕边缘吸附都会走这里）。"""
        if self.enabled:
            self._notify_zoom()

    def _nccalcsize(self, hwnd, wparam, lparam):
        """客户区 = 窗口矩形去掉左右下的系统边框；最大化时用工作区。

        ⚠️ 进来时**rgrc[0] 才是新的窗口矩形**（rgrc[1] 是上一次的）—— 这是踩得最久的坑：
        照文档用 rgrc[1] 会让客户区永远慢窗口一拍，改大之后内容还按旧尺寸排版，
        右半边和底部直接被裁掉。系统拿 rgrc[0] 当「建议客户区」，而我们正要把它
        整个改写成「窗口矩形内缩边框」，所以直接拿它当基准最省事。
        """
        params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
        new_win = params.rgrc[0]
        box = (new_win.left, new_win.top, new_win.right, new_win.bottom)
        res = _u32.CallWindowProcW(self._old, hwnd, WM_NCCALCSIZE, wparam, lparam)
        if _u32.IsZoomed(hwnd):
            # 系统最大化时窗口矩形是「屏幕 + 一圈藏在屏幕外的边框」，
            # 用它就会顶掉任务栏，必须换成工作区。
            x, y, w, h = work_area(self.root)
            params.rgrc[0] = _RECT(x, y, x + w, y + h)
        else:
            dx, dy = self._border()
            left, top, right, bottom = box
            params.rgrc[0] = _RECT(left + dx // 2, top, right - dx // 2, bottom - dy)
        return 0 if res is None else int(res)

    # ------------------------------------------------------------ 拖动标题栏
    def bind_drag(self, *widgets):
        """把这些控件当作标题栏：按住拖动窗口，双击最大化 / 还原。"""
        for w in widgets:
            w.bind("<ButtonPress-1>", self._drag_start, add="+")
            w.bind("<B1-Motion>", self._drag_move, add="+")
            w.bind("<ButtonRelease-1>", self._drag_end, add="+")
            w.bind("<Double-Button-1>", self._drag_toggle, add="+")

    def is_maximized(self):
        """自己是「铺满工作区」就够；另外系统用 Win+↑ 之类接管时也算最大化。"""
        if self._maxed:
            return True
        if not (_IS_WIN and self._hwnd):
            try:
                return self.root.state() == "zoomed"
            except Exception:
                return False
        try:
            return bool(_u32.IsZoomed(self._hwnd))
        except Exception:
            return False

    def _notify_zoom(self):
        zoomed = self.is_maximized()
        if zoomed == self._zoomed:
            return
        self._zoomed = zoomed
        if self._on_zoom:
            try:
                self._on_zoom(zoomed)
            except Exception:
                pass

    def _move_to(self, x, y):
        """只挪位置：`SetWindowPos` 不带 FRAMECHANGED，窗口内容一个像素都不重画。

        ⚠️ 这里千万不能用 `wm geometry("+x+y")` —— 见 _drag_move 的说明。
        """
        if _IS_WIN and self._hwnd:
            dx, dy = self._border()
            try:
                _u32.SetWindowPos(self._hwnd, None, int(x - dx // 2), int(y),
                                  int(self.root.winfo_width() + dx),
                                  int(self.root.winfo_height() + dy),
                                  SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                self._last_pos = (int(x), int(y))
                return
            except Exception:
                pass
        self.root.geometry("+%d+%d" % (x, y))
        self._last_pos = (int(x), int(y))

    def _drag_start(self, event):
        if not self.enabled:
            return
        if self.is_maximized():
            # 最大化状态下拖标题栏＝还原（Windows 的规矩）。还原这一步会重排几何，
            # 所以必须等它落定再取位置，否则起点是最大化时的 (0,0)。
            self.restore()
            self.root.update_idletasks()
        # 记**客户区原点**而不是 winfo_x/y：后者混着 wrapper 的偏移，与 _move_to
        # 的坐标口径不一致，拖起来会差半个边框。
        self._drag = [event.x_root, event.y_root,
                      self.root.winfo_rootx(), self.root.winfo_rooty()]
        self._last_pos = (int(self._drag[2]), int(self._drag[3]))

    def _drag_move(self, event):
        """按「相对起点的位移」而不是每次的增量来摆窗口。

        连续事件里任何一次算错都会累积成越来越离谱的偏移（踩过：拖到屏幕左上角
        外面去了），而相对起点算，即使中间丢事件也只会滞后一帧。

        ⚠️ 移动必须走 `_move_to`（SetWindowPos），不能用 `wm geometry`。后者在
        这个「标题栏被抹掉」的窗口上会被 Tk 当成「客户区 + 标题栏」重算一遍，
        每次调用都带一发 FRAMECHANGED：实测**每一步** 3 次 WM_NCCALCSIZE +
        3 次擦背景 + 1 次整窗重绘、单步 307 ms（普通窗口是 0 条重绘消息）。
        窗口已经挪走、内容还没画完的那段间隙，屏幕上就是一片黑 —— 这就是
        「拖动窗口闪黑屏」的真正来源。换成 SetWindowPos 后同一步只要 1.5 ms，
        重绘消息 0 条。顺带：wm geometry 还会按它估的边框把窗口往右多推 8px。
        """
        if not self._drag or not self.enabled:
            return
        x0, y0, wx, wy = self._drag
        x, y = wx + event.x_root - x0, wy + event.y_root - y0
        # 同一像素内的重复事件直接丢掉：鼠标 125Hz 时会有一堆重复坐标
        if self._last_pos == (int(x), int(y)):
            return
        self._move_to(x, y)

    def _drag_end(self, event):
        self._drag = None

    def _drag_toggle(self, event):
        if not self.enabled:
            return
        self.toggle_max()
        return "break"

    # ------------------------------------------------------------ 窗口键
    def toggle_max(self):
        if self.is_maximized():
            self.restore()
        else:
            # 只有用户主动放大时才更新「还原框」：启动那一次是程序自己放大的，
            # 此时的 winfo 还停在 (0,0)，记下来会让还原位置跑到屏幕左上角
            self._remember_box()
            self.maximize()

    # ------------------------------------------------------------ 摆位
    def _border(self):
        """客户区相对窗口矩形的内缩量 (左右合计, 上下合计)。

        不用 GetSystemMetrics 的理论值：系统在 WM_NCCALCSIZE 返回之后还会自己再缩一圈
        （Win10/11 的 invisible border），实测窗口 716 宽时客户区只有 684 —— 比
        「减两个 8px」多缩了一次。所以一律用**实测值**，随系统版本自适应。
        """
        if self._inset:
            return self._inset
        return (16, 8)

    def measure(self):
        """量一次「窗口 - 客户区」的差并记住。必须在窗口上屏、客户区结算完之后调。"""
        if not (_IS_WIN and self._hwnd):
            return
        try:
            wr = _RECT()
            cr = _RECT()
            _u32.GetWindowRect(self._hwnd, ctypes.byref(wr))
            _u32.GetClientRect(self._hwnd, ctypes.byref(cr))
            dx = (wr.right - wr.left) - (cr.right - cr.left)
            dy = (wr.bottom - wr.top) - (cr.bottom - cr.top)
            if 0 <= dx < 96 and 0 <= dy < 160:
                self._inset = (dx, dy)
        except Exception:
            pass

    def _place(self, x, y, w, h):
        """按**客户区**矩形摆窗口。

        不用 `wm geometry`：Tk 在 Windows 上会把请求当成「客户区 + 标题栏 + 边框」，
        而标题栏已经被我们抹掉了 —— 每次设完都比要求高出一截，还会跟系统还原动画
        抢着改尺寸。用 SetWindowPos 直接摆外框，偏差取实测值。
        """
        if _IS_WIN and self._hwnd:
            dx, dy = self._border()
            try:
                # FRAMECHANGED 不能省：不带它系统只改外框、不重算客户区，
                # 于是窗口变大了而内容还缩在左上角
                _u32.SetWindowPos(self._hwnd, None, int(x - dx // 2), int(y),
                                  int(w + dx), int(h + dy),
                                  SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
                self._last_pos = (int(x), int(y))
                return
            except Exception:
                pass
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self._last_pos = (int(x), int(y))

    def maximize(self):
        """铺满工作区（扣掉任务栏）。

        走自己的摆位而不调 `state("zoomed")`：系统最大化会把窗口矩形摊到屏幕外一圈，
        再叠上还原动画，尺寸就再也对不齐了（踩过：还原后高度在 373 / 548 / 720 之间乱跳）。
        """
        x, y, w, h = work_area(self.root)
        self._maxed = True
        self._place(x, y, w, h)
        self._notify_zoom()

    def restore(self):
        box = self._restore_box or (0, 0, 1080, 700)
        if _IS_WIN and self._hwnd and _u32.IsZoomed(self._hwnd):
            # 用户可能是用 Win+↑ / 双击让它变成「系统最大化」的，得先退出那个状态
            try:
                _u32.ShowWindow(self._hwnd, SW_RESTORE)
            except Exception:
                self.root.state("normal")
        self._maxed = False
        self._place(*box)
        self._notify_zoom()

    def _remember_box(self):
        """记下「还原」该回到哪儿。窗口还没上屏时 winfo 是一堆 1，别拿它覆盖。

        位置取客户区原点（winfo_rootx/rooty）：_place 收到的就是这个口径，
        用 winfo_x/y 会差半个边框，还原一次就往右挪一点。
        """
        if self._maxed:
            return
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w > 100 and h > 100:
            self._restore_box = (self.root.winfo_rootx(), self.root.winfo_rooty(), w, h)

    def minimize(self):
        """最小化。要自己记着位置：Windows 把最小化的窗口挪到屏幕外（-32000 那带），
        Tk 的 deiconify 又会照着这个坐标把窗口摆回来 —— 从任务栏点回来时
        窗口就整个跑到显示器外面去了。"""
        try:
            if not self.is_maximized():
                self._icon_pos = (self.root.winfo_rootx(), self.root.winfo_rooty())
            self.root.iconify()
        except Exception:
            pass

    def _on_map(self, event):
        """窗口重新上屏（从任务栏回来）时把位置摆回最小化之前。"""
        if event.widget is not self.root or self._icon_pos is None:
            return
        x, y = self._icon_pos
        self._icon_pos = None
        try:
            if not self.is_maximized():
                self._move_to(x, y)
        except Exception:
            pass

    def close(self):
        self.uninstall()
        self.root.destroy()
