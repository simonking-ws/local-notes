<p align="center">
  <img src="icon/app_icon.png" width="140" alt="素笺 图标">
</p>

<h1 align="center">素笺（local-notes）</h1>

<p align="center">
  <em>复刻 macOS 笔记的本地桌面笔记应用 —— Python + Tkinter，零第三方依赖，默认不联网。</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.x-blue" alt="Python 3">
  <img src="https://img.shields.io/badge/依赖-无-brightgreen" alt="零依赖">
  <img src="https://img.shields.io/badge/存储-本地%20SQLite-informational" alt="本地存储">
  <img src="https://img.shields.io/badge/License-Apache%202.0-green" alt="License">
</p>

---

## 简介

**素笺**（仓库名 `local-notes`）是一个致敬 Apple Notes（macOS 笔记）的桌面笔记程序。经典三栏布局——顶部工具栏、左侧文件夹与笔记列表、右侧富文本编辑器，风格简洁克制，专注于「打开就能写」的流畅体验。

与大多数笔记软件不同，素笺**不依赖任何第三方库，也不连接任何云端服务**——只需要电脑上自带的 Python 即可运行。所有文件夹、笔记和设置都保存在本地的 SQLite 数据库中，配合一键导出备份（默认压缩的 `.json.gz`，也可存成可读的 `.json`），**数据的所有权完全归你自己**。

## 界面预览

<p align="center">
  <img src="images/01.png" alt="素笺主界面：文件夹 / 笔记列表 / 编辑器三栏布局">
</p>
<p align="center"><sub>经典三栏布局：左侧文件夹与笔记列表，右侧富文本编辑器</sub></p>

## 主要功能

**写作与排版**
- 富文本编辑：加粗 / 斜体 / 下划线 / 删除线、两级标题、文字与下划线颜色，样式随笔记保存并精确还原
- 四种列表（项目符号 / 有序 / 三角 / 待办勾选框）、引用块、行内代码、分割线，标记与正文采用同一套缩进标尺
- 第一行恒为标题；`Ctrl+D` / `Ctrl+Y` 整行复制与删除（连同格式与勾选状态）
- markdown 快捷输入：段首敲 `# `、`## `、`- `、`1. `、`> `、`-[] `、`-> `、三个反引号或 `---` 即就地成文，结果只留样式

**效率输入**
- **@ 关联笔记**：`@` + 空格浮出候选（最近修改优先），插入蓝色 `@标题`，单击跳转到目标笔记；存的是 id，改名 / 挪文件夹都不断链
- **超链接**：`[文字](网址)` + 空格直接变蓝，指针悬停浮出小窗可改文字与地址；裸网址同样可点
- **时间函数**：`date()` / `datetime()` + 空格取当前时间
- **公式计算**：`$5+6=` + 空格 → `5+6= 11`，支持括号、`^`、`%`、`sin/cos/tan`，算完即删触发符
- **任务提醒**：正文里插入闹钟图标，到点右下角浮出置顶卡片（日期用日历、时间用时分选择框）

**代码块**
- 内置代码块，顶部语言条随时切换：纯文本 / JSON / XML / HTML / Java / Python / Bash / SQL，等宽字体 + 轻量语法高亮
- 一键复制、一键格式化：JSON / HTML / XML 结构化重排，SQL 按关键词断行（每个字段各占一行），Java / Python / Bash 重排缩进
- 格式化前统一中文标点（仅 JSON / SQL），字符串与注释内部原样保留

**整理与检索**
- 左栏两组：**智能分类**（全部 / 星标 / 加密，按条件筛的视图）+ **文件夹**（可建多级，最多 5 层）
- 多级文件夹：缩进 + 行首箭头收起 / 展开，父级计数与视图包含整棵子树；支持全部展开 / 全部收起、拖拽移动、右键「移动笔记到…」目标树
- 全文搜索即时过滤（SQLite FTS5，环境不支持时回退 LIKE），命中词在正文里标黄
- 「跳到所属文件夹」：在聚合视图或父视图里右键笔记，一步回到它真正所在的位置
- 星标、置顶

**多标签页**
- 点列表里的笔记开成一枚标签，重复点击切回已有的那枚；`Ctrl+Tab` 切换、`Ctrl+W` 关闭、右键关闭其它，标签随设置持久化

**笔记加密**
- 任意笔记可设加密口令：正文在数据库里只留密文，搜索用明文列一并清空
- PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）派生密钥，再以 SHA-256 作 PRF 的 CTR 加密 + HMAC 认证，先验签再解密，全部只用标准库
- 切走即重新封上；口令只留在内存、退出即忘，不落盘也无法找回

**数据与备份**
- 本地 SQLite 单文件存储，旧版 JSON 数据首次启动自动迁移
- 单篇导出 `.txt` / `.md` / `.pdf`；整库备份导出 `.json.gz` / `.json`，导入支持覆盖或合并（按正文指纹去重）
- 保存失败时另存 `data/pending_*.json` 兜底快照，下次启动提示可恢复
- 单实例运行，避免两个窗口同时改写同一个库

**外观与细节**
- 左栏两列可拖宽（双击分隔条恢复默认）；工具栏可折叠、可自定义显示哪几枚按钮
- 可读取 WorkBuddy 皮肤自动适配配色；悬停 / 选中 / 浮层的状态色按语义推导，换主题不撞色
- 圆角自绘 UI、长标题悬停跑马灯、顶栏即窗口标题栏
- 手动「检查更新」：读 GitHub Releases 比对版本，后台下载支持断点续传，替换只覆盖程序文件（`data/` 一个字节不动）

## 快速开始

无需安装任何依赖，电脑上有 Python 3 即可运行：

```bash
python scripts/notes_app.py
```

启动后会在项目根目录的 `data/` 下自动创建 `notes_data.db` 数据库文件。

运行测试（`python -m unittest discover -s tests`）与打包 exe（`target/build.bat`）见 [开发与打包](docs/开发与打包.md)。

> 项目图标即仓库中的 [`icon/app_icon.png`](icon/app_icon.png)，同时作为应用窗口 / 任务栏图标（由 `scripts/notes_app.py` 启动时加载）。

## 文档

详细用法、数据说明与开发指引都在 [`docs/`](docs/README.md) 下：

| 文档 | 内容 |
| --- | --- |
| [使用指南](docs/使用指南.md) | 界面与操作、文件夹整理、编辑排版、代码块、加密、提醒、搜索 |
| [快捷键](docs/快捷键.md) | 全部快捷键、markdown 快捷输入一览 |
| [数据与备份](docs/数据与备份.md) | 存储位置、导出 / 导入、保存兜底、检查更新与网络访问 |
| [开发与打包](docs/开发与打包.md) | 目录结构、分层依赖、测试、打包 exe、PDF 导出原理 |

## 技术栈

- **语言**：Python 3 ｜ **GUI**：Tkinter ｜ **数据**：SQLite3（FTS5 + trigram，回退 LIKE）——全部标准库
- **分层**：`store`（数据）→ `theme`（配色）→ `widgets`（控件）→ `app_ui` / `app_actions`（界面 / 动作）→ `notes_app`（入口）
- **测试**：标准库 unittest，`python -m unittest discover -s tests`

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。
