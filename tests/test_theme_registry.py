# -*- coding: utf-8 -*-
"""主题注册表回归测试：每套主题都须满足基本可读性，且派生状态色彼此可区分。

不建 GUI：全部走 theme.py 的纯函数（build_palette + 颜色运算），断言对比度 /
色距达标。跑法：
    python tests/test_theme_registry.py          # 独立运行，打印明细 + 结论
    python -m pytest tests/test_theme_registry.py # 走 pytest
"""
import sys
import os
import unittest

# 让脚本能 import 同仓的 scripts/ 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from theme import (  # noqa: E402
    THEMES, THEME_LABELS, THEME_ORDER, theme_label, build_palette, list_themes,
    DEFAULT_THEME, skin_accent, skin_look,
    FOREST_ACCENT_FALLBACK,
    _contrast_ratio, _color_dist, _luminance, _hex_rgb, on_accent,
    _status_bar_bg, _readonly_bar_bg, _tab_idle_bg, _toolbar_bg, _sel_bg,
)


def _sel_fg(pal):
    """复刻 widgets._sel_fg，避免在测试里拉起整个 widgets（含 tkinter 控件树）。"""
    sel = pal.get("sel", "#cfe0fb")
    text = pal.get("text", "#1f1f24")
    if abs(_luminance(sel) - _luminance(text)) >= 0.34:
        return text
    return "#ffffff" if _luminance(sel) < 0.5 else "#1f2328"


def _check_theme(tid):
    p = build_palette(tid)
    problems = []

    def need(name, ok, detail=""):
        if not ok:
            problems.append("%s %s" % (name, detail))

    # 1) 正文 / 次级文字 / 链接：在稿纸上的对比度（WCAG AA 正文 4.5）
    need("text/editor", _contrast_ratio(p["text"], p["editor"]) >= 4.5,
         "=%.2f" % _contrast_ratio(p["text"], p["editor"]))
    need("soft/editor", _contrast_ratio(p["soft"], p["editor"]) >= 4.5,
         "=%.2f" % _contrast_ratio(p["soft"], p["editor"]))
    need("link/editor", _contrast_ratio(p["link"], p["editor"]) >= 4.5,
         "=%.2f" % _contrast_ratio(p["link"], p["editor"]))

    # 2) 悬停 与 选中 必须可区分（一个是明度变化、一个带主题色相）
    need("hover!=sel", _color_dist(p["hover"], p["sel"]) >= 12,
         "dist=%d" % _color_dist(p["hover"], p["sel"]))

    # 3) 选中行底色明显压得住所在列表底
    need("sel/list", _contrast_ratio(p["sel"], p["list"]) >= 1.3,
         "=%.2f" % _contrast_ratio(p["sel"], p["list"]))

    # 4) 底部状态条：与稿纸有反差，且不是稿纸本尊
    fbg = _status_bar_bg(p)
    need("footer!=editor", _contrast_ratio(fbg, p["editor"]) >= 1.2,
         "=%.2f" % _contrast_ratio(fbg, p["editor"]))

    # 5) 只读提示条 与 未选中标签 不撞色（与 _readonly_bar_bg 内部兜底的基准一致：
    # 它比的是「工具栏底色推导出的未选中标签」，不是 p["side"] 本身）
    ro = _readonly_bar_bg(p)
    idle = _tab_idle_bg(_toolbar_bg(p))
    need("ro!=tab_idle", _color_dist(ro, idle) >= 15,
         "dist=%d" % _color_dist(ro, idle))

    # 6) 选中文字（选区）在选区底上读得清
    sfg = _sel_fg(p)
    sb = _sel_bg(p)
    need("sel_fg/sel_bg", _contrast_ratio(sfg, sb) >= 4.5,
         "=%.2f" % _contrast_ratio(sfg, sb))

    return problems


def test_all_themes_readable():
    all_ok = True
    for tid, label in list_themes():
        problems = _check_theme(tid)
        status = "OK" if not problems else "FAIL"
        if problems:
            all_ok = False
        print("[%s] %-10s %-14s %s" % (status, tid, label,
              "; ".join(problems) if problems else "contrast & state colors pass"))
    assert all_ok, "有主题未通过可读性 / 状态色校验，见上面 FAIL 项"


def test_theme_labels():
    """显示名是 UI 文案，与持久化的主题 id 解耦：改文案不许动 id、不许留旧称。"""
    assert THEME_LABELS["light"] == "云母白", "浅色主题的显示名应是「云母白」"
    assert THEME_LABELS["dark"] == "石墨灰", "深色主题的显示名应是「石墨灰」"
    assert theme_label("light") == "云母白" and theme_label("dark") == "石墨灰"
    # id 不变：老用户库里 settings["theme"] == "light"/"dark" 仍能正确读回主题
    assert set(THEME_LABELS) == set(THEMES) == {
        "light", "dark", "sepia", "forest", "midnight", "sky"}
    assert THEME_LABELS["sky"] == "晴空蓝", "第六套主题的显示名应是「晴空蓝」"
    assert [lb for _tid, lb in list_themes()] == [
        "森林绿", "云母白", "石墨灰", "羊皮纸", "午夜蓝", "晴空蓝"], \
        "色卡顺序或文案不对（默认主题森林绿排首位，其余保持原相对顺序）"


def test_theme_display_order():
    """设置窗色卡的展示顺序：默认主题森林绿第一格，其余保持原相对顺序。

    只调展示顺序，不动注册表 THEMES 的历史顺序（新主题仍追加末尾）、不动 id、
    不动落库值；THEME_ORDER 必须覆盖全部主题且不重复，漏登记的新主题补在末尾。
    """
    ids = [tid for tid, _lb in list_themes()]
    assert ids[0] == DEFAULT_THEME == "forest", "森林绿（默认主题）必须排第一格"
    assert ids[1:] == ["light", "dark", "sepia", "midnight", "sky"], \
        "其余五套仍按原相对顺序排，别顺手挪位置"
    assert len(ids) == len(set(ids)) == len(THEMES), "展示顺序漏了或多算了主题"
    assert tuple(ids) == THEME_ORDER, "THEME_ORDER 与实际展示顺序不一致"
    # 新主题忘了登记进 THEME_ORDER：兜底按注册表顺序补到末尾，不许静默丢一套
    THEMES["__probe__"] = {"base": THEMES["light"]["base"]}
    try:
        assert [t for t, _lb in list_themes()][-1] == "__probe__", \
            "THEME_ORDER 漏登记的新主题应补在末尾"
    finally:
        THEMES.pop("__probe__", None)


def test_default_theme_is_forest_on_first_run():
    """首次打开软件的默认色 = 森林绿：常量、兜底、首次落库三处都得钉住。"""
    import notes_app
    assert DEFAULT_THEME == "forest", "首次打开软件的默认色必须是森林绿"
    assert build_palette("unknown-theme") == build_palette("forest")
    # 全新库：第一次启动就把默认写进 settings，日后改 DEFAULT_THEME 不动老用户
    fresh = {"settings": {}}
    assert notes_app._ensure_default_theme(fresh) is True, "首次运行应落库默认主题"
    assert fresh["settings"]["theme"] == "forest"
    assert notes_app._ensure_default_theme(fresh) is False, "已落库就不该重复写"
    # 用户自己选过的主题不许被默认覆盖
    chosen = {"settings": {"theme": "sepia"}}
    assert notes_app._ensure_default_theme(chosen) is False
    assert chosen["settings"]["theme"] == "sepia"
    # 已下线的 skin 归位默认；settings 不是 dict 的坏数据也要能修好
    stale = {"settings": {"theme": "skin"}}
    assert notes_app._ensure_default_theme(stale) is True
    assert stale["settings"]["theme"] == "forest"
    broken = {"settings": "oops"}
    assert notes_app._ensure_default_theme(broken) is True
    assert broken["settings"]["theme"] == "forest"


def test_skin_theme_removed():
    """「跟随 WorkBuddy 外观（skin）」已下线：列表里没有它，旧库里的值落到默认主题。"""
    ids = [tid for tid, _label in list_themes()]
    assert "skin" not in ids, "skin 主题应已下线，不该再出现在外观主题里"
    assert DEFAULT_THEME in ids, "默认主题必须在列表里，否则下拉框会选不中"
    # 旧数据（settings["theme"] == "skin"）读到时不认识 → 回退 DEFAULT_THEME
    assert build_palette("skin") == build_palette(DEFAULT_THEME), \
        "旧的 skin 主题必须等价于默认主题，不能让老用户升级后看到另一套配色"


def test_dark_themes_distinct():
    """两套深色主题必须一眼能分开：石墨灰＝中性灰 + 暖金，午夜蓝＝藏蓝 + 靛蓝。

    以前两者都是冷调深灰蓝、只差几档明度（#26262c vs #1d2330），下拉里切过去
    几乎看不出变化。这里用「冷暖差 r-b」把语义钉住：谁再往回调淡蓝度就会失败。
    """
    def warmth(c):
        r, _g, b = _hex_rgb(c)
        return r - b

    d, m = build_palette("dark"), build_palette("midnight")

    # 1) accent：一暖一冷，色相相反（金色 vs 靛蓝）
    assert warmth(d["accent"]) >= 40, "石墨灰的 accent 应是暖金（r-b 明显为正），实际 %s" % d["accent"]
    assert warmth(m["accent"]) <= -40, "午夜蓝的 accent 应是靛蓝（r-b 明显为负），实际 %s" % m["accent"]

    # 2) 底色：石墨灰三通道基本相等（中性灰），午夜蓝必须明显偏蓝
    for key in ("top", "list", "editor"):
        assert abs(warmth(d[key])) <= 8, \
            "石墨灰的 %s 应是中性灰、不带色相，实际 %s" % (key, d[key])
        assert warmth(m[key]) <= -12, \
            "午夜蓝的 %s 应明显偏蓝（r-b ≤ -12），实际 %s" % (key, m[key])

    # 3) 实际用到的选中行也要能分开（灰底暖金 vs 蓝底靛蓝）
    dist = _color_dist(d["sel"], m["sel"])
    assert dist >= 40, "两套深色的选中行太接近：dist=%d" % dist


def test_on_accent_readable():
    """实心 accent 块上的文字色：每套主题都得读得清，且老主题仍用白字（观感不变）。"""
    for tid, _label in list_themes():
        p = build_palette(tid)
        fg = on_accent(p["accent"])
        ratio = _contrast_ratio(fg, p["accent"])
        assert ratio >= 3.0, "%s 的 accent 按钮文字对比不足：%.2f" % (tid, ratio)
        if tid != "dark":
            assert fg == "#ffffff", "%s 的 accent 是深色，应继续用白字（现为 %s）" % (tid, fg)
    # 金色 accent 必须落到墨字，否则「确定 / 知道了」这类实心按钮会糊成一片
    assert on_accent(THEMES["dark"]["base"]["accent"]) != "#ffffff", \
        "石墨灰的琥珀金 accent 上应改墨字"


def test_forest_uses_workbuddy_accent():
    """森林绿的 accent 必须等于 WorkBuddy 当前主题色；读不到才用自带的绿。"""
    acc = skin_accent(None)
    p = build_palette("forest")
    if acc:
        assert p["accent"] == acc, "森林绿没能拿到 WorkBuddy 的主题色（拿到的是 %s）" % p["accent"]
    else:
        assert p["accent"] == FOREST_ACCENT_FALLBACK, "读不到皮肤时应退回自带的绿"
    # 其它主题的 accent 不受皮肤影响（防止误把整套皮肤灌进去）
    for tid in ("light", "dark", "sepia", "midnight", "sky"):
        assert build_palette(tid)["accent"] == THEMES[tid]["base"]["accent"], \
            "%s 的 accent 不该跟着 WorkBuddy 变" % tid
    # 选中行文字跟着换过的 accent 走，且在选中底上读得清
    ratio = _contrast_ratio(p["selfg"], p["sel"])
    assert ratio >= 4.5, "森林绿选中行文字对比不足：%.2f" % ratio


def test_forest_follows_skin_surfaces():
    """森林绿的顶栏 / 侧栏 / 列表 / 稿纸 / 字色必须跟 WorkBuddy 外观走（不只是 accent）。"""
    look = skin_look()
    p = build_palette("forest")
    required = ("top", "side", "list", "editor", "text")
    if all(k in look for k in required):
        for k in ("top", "side", "list", "editor", "line", "text", "soft"):
            if k in look:
                assert p[k] == look[k], "森林绿的 %s 没跟 WorkBuddy 外观走（%s != %s）" % (
                    k, p[k], look[k])
    else:
        # 读不到皮肤的机器：整窗退回 FOREST 自带底色，别混出半套色
        for k in required:
            assert p[k] == THEMES["forest"]["base"][k], \
                "没有皮肤时 %s 应退回自带底色" % k


def test_sky_matches_reference():
    """晴空蓝必须还原参考图：整窗浅天蓝 + 近白列表 + 白稿纸 + 亮蓝强调。

    色值是按截图逐区取色定的，不是随手调的蓝；改配色前先回头看这张图。
    另外钉住两个「差一点点就翻车」的边界：亮蓝 accent 上的白字对比、
    选中行底与列表底的反差（见 theme.SKY 的说明）。
    """
    p = build_palette("sky")
    assert p["top"] == "#ccebff" and p["side"] == "#ccebff", \
        "晴空蓝的顶栏 / 侧栏应是浅天蓝 #ccebff"
    assert p["list"] == "#f7fcff" and p["editor"] == "#ffffff", \
        "晴空蓝应是近白列表 + 纯白稿纸"
    assert p["text"] == "#1e3b4f" and p["soft"] == "#506776", \
        "晴空蓝的主字 / 次级字应是截图里的深蓝灰"
    assert _color_dist(p["accent"], "#0099ff") <= 10, \
        "强调色应是截图那个亮天蓝，实际 %s" % p["accent"]
    assert _color_dist(p["sel"], "#bce4ff") <= 8, \
        "选中行应贴近截图的 #bce4ff，实际 %s" % p["sel"]
    # #0099ff 上白字只有 2.9998，`on_accent` 会判成墨字；微调后的亮蓝要稳稳用白字
    assert _contrast_ratio("#ffffff", p["accent"]) >= 3.0
    assert on_accent(p["accent"]) == "#ffffff", \
        "亮蓝实心按钮上应是白字，别变成黑字"
    assert _contrast_ratio(p["selfg"], p["sel"]) >= 4.5, \
        "选中行文字在浅蓝底上要够读"
    assert not _check_theme("sky"), "晴空蓝没过通用可读性校验：%s" % _check_theme("sky")
    # 和「同样带蓝」的午夜蓝、以及中性浅色的云母白都必须一眼分开。
    # 只比静态 base：森林绿的整窗底色跟着 WorkBuddy 皮肤跑，拿它当基准会随机器变。
    for other in ("light", "midnight"):
        assert _color_dist(p["top"], build_palette(other)["top"]) >= 20, \
            "晴空蓝的顶栏和 %s 太接近" % other


class SkyThemeTest(unittest.TestCase):
    """让 unittest discover 也能收集到「晴空蓝还原参考图」这组约束。"""

    def test_sky_matches_reference(self):
        test_sky_matches_reference()

    def test_labels_and_order(self):
        test_theme_labels()


class ThemeOrderTest(unittest.TestCase):
    """让 unittest discover 也能收集到「色卡展示顺序」这组约束。"""

    def test_display_order(self):
        test_theme_display_order()


class DefaultThemeTest(unittest.TestCase):
    """让 unittest discover 也能收集到「首次默认森林绿」这条约束。"""

    def test_first_run_default_is_forest(self):
        test_default_theme_is_forest_on_first_run()


if __name__ == "__main__":
    # 独立运行时打印完整明细，方便微调取色
    print("%-6s %-10s %-14s %-8s %-8s %-8s %-8s" %
          ("id", "", "label", "txt/ed", "soft/ed", "lnk/ed", "sel/list"))
    for tid, label in list_themes():
        p = build_palette(tid)
        print("%-6s %-10s %-14s %-8.2f %-8.2f %-8.2f %-8.2f" % (
            tid, "", label,
            _contrast_ratio(p["text"], p["editor"]),
            _contrast_ratio(p["soft"], p["editor"]),
            _contrast_ratio(p["link"], p["editor"]),
            _contrast_ratio(p["sel"], p["list"]),
        ))
    print("")
    ok = True
    for fn in (test_all_themes_readable, test_theme_labels,
               test_theme_display_order,
               test_default_theme_is_forest_on_first_run,
               test_skin_theme_removed, test_sky_matches_reference,
               test_dark_themes_distinct, test_on_accent_readable,
               test_forest_uses_workbuddy_accent, test_forest_follows_skin_surfaces):
        try:
            fn()
            print("[OK]   %s" % fn.__name__)
        except AssertionError as e:
            ok = False
            print("[FAIL] %s  %s" % (fn.__name__, e))
    if not ok:
        sys.exit(1)
    print("\nALL THEMES PASS")
