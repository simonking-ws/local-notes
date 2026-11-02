# -*- coding: utf-8 -*-
"""换肤后正文里已嵌入的闹钟图标应就地重染（按 star 染色）。

不建 GUI：用 Mock 顶替 Text 控件，验证 set_theme 触发的 _refresh_alarm_images
会对每个已注册闹钟名调用 image_configure 重染，并清掉旧缓存迫使重新染色。
跑法：
    python tests/test_theme_alarm.py            # 独立运行
    python -m pytest tests/test_theme_alarm.py  # 走 pytest
"""
import sys
import os
import types
from unittest import mock

# 让脚本能 import 同仓的 scripts/ 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import app_actions          # noqa: E402
import notes_app            # noqa: E402


def _alarm_owner_cls():
    cls = [c for c in app_actions.__dict__.values()
           if isinstance(c, type) and hasattr(c, "_refresh_alarm_images")]
    assert cls, "未找到持有 _refresh_alarm_images 的类"
    return cls[0]


def test_refresh_alarm_images_recolors_embedded():
    cls = _alarm_owner_cls()
    new_img = object()                 # 新色图（按新主题 star 染色）
    fake = types.SimpleNamespace()
    fake._alarm_img = object()         # 旧缓存，须被清掉
    fake._alarms = {"alarm:a": {}, "alarm:b": {}}
    fake._alarms_get = lambda: fake._alarms
    fake.text = mock.MagicMock()
    fake._alarm_photo = lambda: new_img

    cls._refresh_alarm_images(fake)

    # 1) 缓存被清，迫使 _alarm_photo 重新染色
    assert fake._alarm_img is None
    # 2) 每个已嵌入闹钟都按名 image_configure 到新图
    called = {c.args[0] for c in fake.text.image_configure.call_args_list}
    assert called == {"alarm:a", "alarm:b"}, called
    for c in fake.text.image_configure.call_args_list:
        assert c.kwargs.get("image") is new_img, c.kwargs


def test_refresh_alarm_images_no_text_is_safe():
    cls = _alarm_owner_cls()
    fake = types.SimpleNamespace()
    fake._alarm_img = object()
    fake._alarms = {}
    fake._alarms_get = lambda: fake._alarms
    fake.text = None                    # 极端情况：编辑器还没建好
    fake._alarm_photo = lambda: object()
    # 不应抛异常
    cls._refresh_alarm_images(fake)
    assert fake._alarm_img is None


def test_set_theme_invokes_refresh():
    import inspect
    src = inspect.getsource(notes_app.NotesApp.set_theme)
    # 换肤必须触发已嵌入闹钟重染
    assert "self._refresh_alarm_images()" in src
    # 旧的“只清缓存”写法应已移除，否则残留旧色
    assert "self._alarm_img = None" not in src


if __name__ == "__main__":
    test_refresh_alarm_images_recolors_embedded()
    test_refresh_alarm_images_no_text_is_safe()
    test_set_theme_invokes_refresh()
    print("ALL ALARM REFRESH TESTS PASSED")
