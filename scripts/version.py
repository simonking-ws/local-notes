# -*- coding: utf-8 -*-
"""素笺的版本号 —— 全项目唯一来源。

为什么单独拆一个文件，而不是塞进 store.py 或 notes_app.py：
这个字符串要同时出现在**三处** —— 「关于」弹窗里显示、GitHub release 的 tag、
打包后 exe 的文件属性（build.py 读它生成 version-file）。三处各抄一份必然漂移，
最后就是「属性里写 1.2、弹窗里写 1.1、tag 是 1.0」这种谁都不信的场面。
所以只留这一份，谁要谁 import。

发版流程：改这里的数字 → 打 tag ``v1.1.0`` → 上传 zip 到 Release。
tag 的 ``v`` 前缀可有可无，比较时一律去掉（仓库里最早那个 release 的 tag
就是纯数字 ``1.0``，得兼容）。
"""

__version__ = "1.0.0"


def as_tuple(v=None):
    """把 "v1.1.0" / "1.0" / "1.1.0-beta.2" 转成**等长**的四元组，可直接比大小。

    必须补零到等长：Python 里 ``(1, 0) < (1, 0, 0)`` 恒为 True，而线上最早的
    tag 恰好是两位的 ``1.0`` —— 不补零的话「1.0.0 和 1.0 其实是同一个版本」
    这个判断永远不成立，用户会被反复提示「发现新版本」，点了更新也还是提示。

    容错范围到「带前缀 / 带预发布后缀 / 段里有脏字符」为止：更新检查是后台
    悄悄跑的，不该因为作者手滑写了个 ``v1.1.0-beta`` 就抛异常。
    """
    s = str(__version__ if v is None else v).strip()
    if not s:
        return (0, 0, 0, 0)
    # 先切掉预发布/构建后缀（-beta.1、+20260101），它们不参与「谁更新」的判断
    core = s.lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for seg in core.split(".")[:4]:
        if seg.isdigit():
            parts.append(int(seg))
        else:
            # "1.1b" 这种手滑：取段内的数字，取不到就当 0
            digits = "".join(ch for ch in seg if ch.isdigit())
            parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (4 - len(parts)))


def is_newer(remote, local=None):
    """远端版本是否比本机新。相等、更旧、解析不出来——一律 False。

    宁可漏报（用户自己去看 release 页）也不能误报：误报意味着每次启动都
    弹一句「有新版本」，而那个「新版本」其实早就装上了。
    """
    return as_tuple(remote) > as_tuple(local)
