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
VERSION_FILE = os.path.join(TARGET_DIR, "file_version_info.txt")

# 版本号只有 scripts/version.py 一份，打包时直接读它（不在这里再抄一遍数字）
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from version import __version__, as_tuple       # noqa: E402


def write_version_file():
    """生成 PyInstaller 的版本资源，让 exe 的属性框里显示得出版本号。

    没有这一步的后果很具体：「关于」弹窗里写着 v1.1.0，而资源管理器属性页里
    是空的（或者还是 0.0.0.0），用户报问题时说的版本和实际装的版本对不上。
    「检查更新」上线后这一项更要紧 —— 判断有没有新版全靠这个数字。

    字符串表用 080404b0（简体中文 + Unicode 代码页 1200），这样属性页里
    中文 ProductName 不会变成问号。文件本身用 UTF-8 写，PyInstaller 6 认。
    """
    v = as_tuple(__version__)
    text = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(v)s, prodvers=%(v)s, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('FileDescription', '素笺 - 零依赖的本地笔记'),
        StringStruct('FileVersion', '%(s)s'),
        StringStruct('InternalName', '素笺'),
        StringStruct('OriginalFilename', '素笺.exe'),
        StringStruct('ProductName', '素笺'),
        StringStruct('ProductVersion', '%(s)s')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % {"v": tuple(v), "s": __version__}
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return VERSION_FILE


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
    ver = write_version_file()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                            # 双击不弹黑色控制台窗口
        "--name", APP,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", TARGET_DIR,
        # exe 属性页里的版本号 / 产品名（没有它，属性和「关于」弹窗会互相矛盾）
        "--version-file", ver,
        # 整个 icon 目录随包：运行时窗口图标从 _MEIPASS/icon 取
        "--add-data", "%s%sicon" % (os.path.join(ROOT, "icon"), os.pathsep),
    ]
    if icon:
        args += ["--icon", icon]
    args.append(ENTRY)
    run(args, cwd=ROOT)

    print("\n完成！可执行文件：%s（版本 %s）"
          % (os.path.join(DIST, APP, APP + ".exe"), __version__))


if __name__ == "__main__":
    main()
