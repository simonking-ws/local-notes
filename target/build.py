# -*- coding: utf-8 -*-
"""把「素笺」打包成 Windows 可执行程序（PyInstaller 文件夹模式）。

用法：双击 target\\build.bat，或在本目录执行 `python build.py`。

产物（打包相关的文件全部收拢在 target 目录下）：
    target\\dist\\素笺\\素笺.exe     ← 双击即可打开
    target\\build\\                  ← 打包中间产物
    target\\素笺.spec                ← PyInstaller 配置
"""
import os
import subprocess
import sys

TARGET_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TARGET_DIR)              # 项目根目录
APP = "素笺"
ENTRY = os.path.join(ROOT, "scripts", "notes_app.py")
ICON_PNG = os.path.join(ROOT, "icon", "app_icon.png")
ICON_ICO = os.path.join(ROOT, "icon", "app_icon.ico")
DIST = os.path.join(TARGET_DIR, "dist")
WORK = os.path.join(TARGET_DIR, "build")


def run(cmd, cwd=None):
    print(">>", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


def ensure(pkg, module):
    """缺哪个包就装哪个（PyInstaller / Pillow 只在打包时用，不影响程序本身零依赖）。"""
    try:
        __import__(module)
    except ImportError:
        print("未检测到 %s，正在安装……" % pkg)
        run([sys.executable, "-m", "pip", "install", pkg])


def make_icon():
    """把 512×512 的 PNG 转成多尺寸 .ico，用作 exe 的文件图标；失败则用默认图标。"""
    try:
        from PIL import Image
        im = Image.open(ICON_PNG).convert("RGBA")
        im.save(ICON_ICO, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        return ICON_ICO
    except Exception as e:                       # noqa: BLE001
        print("生成 exe 图标失败，改用默认图标：", e)
        return None


def main():
    ensure("pyinstaller", "PyInstaller")
    ensure("pillow", "PIL")
    icon = make_icon()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                            # 双击不弹黑色控制台窗口
        "--name", APP,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", TARGET_DIR,
        # 整个 icon 目录随包：运行时窗口图标从 _MEIPASS/icon 取
        "--add-data", "%s%sicon" % (os.path.join(ROOT, "icon"), os.pathsep),
    ]
    if icon:
        args += ["--icon", icon]
    args.append(ENTRY)
    run(args, cwd=ROOT)

    print("\n完成！可执行文件：%s" % os.path.join(DIST, APP, APP + ".exe"))


if __name__ == "__main__":
    main()
