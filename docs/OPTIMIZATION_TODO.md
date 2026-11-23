# 素笺项目优化 / 待修改清单

> 排查维度：代码质量/死代码、数据安全/边界、UI 交互一致性、性能/健壮性。
> 已核验高风险项；已剔除有意设计（见各维度说明）。

## 处理状态总览（2026-11-20 按序修复）

| 项 | 状态 | 说明 |
|---|---|---|
| H1 加密笔记图片漏备份 | ✅ 已修 | pack_media 改扫 media 目录；MediaBackupTest 3 例 |
| M1 设置窗取消不回滚主题/字体 | ✅ 已修 | 进窗快照 + cancel 回滚；test_cancel_rolls_back_theme_and_font |
| M2 查找浮条换肤悬停旧色 | ✅ 已修 | 悬停回调运行时取 self.pal；test_find_bar_hover_uses_current_theme_after_switch |
| M3 搜索结果排序不一致 | ✅ 已修 | sorted(hits, note_sort_key)；SearchOrderTest |
| M4 密文 iter 无上限 | ✅ 已修 | secret._check_iters 区间校验；test_absurd_iter_is_rejected |
| M5 性能（4 处） | ✅ 已修 | 文件夹计数 O(F+N)、alarm 复用扫描、GC 空目录快返、搜索高亮去抖 |
| M6 关窗异步回调竞态 | ✅ 已修 | 4 处 canvas.after 改 self._after 登记 |
| M7 导出文件名保留设备名 | ✅ 已修 | safe_filename 加 CON/COM1 等前缀；test_windows_reserved_names |
| M8 解压/下载大小上限 | ✅ 已修 | backup._read_capped(100MB)、imgstore 下载上限 40MB |
| M9 digest 缺失拒装 | ⏭ 跳过 | **实为有意设计**：test_no_digest_still_works 明确要求老 release 无 digest 也能更新 |
| L1 空口令无提示 | ✅ 已修 | 内联 hint；test_empty_password_shows_hint... |
| L2 死代码 | ✅ 部分 | 删未用 ttk 导入、pick_exe 补「浏览」按钮接线；ALARM_TASK_COL 仍被测试引用故保留 |
| L3 跨年昨天判断 | ✅ 已修 | 改 datetime.date 做差 |
| L4 截断工具重复 | ✅ 部分 | store 抽 _char_w 共用、widgets 阈值走 dpi；跨文件三份 _clip 未合并（风险大） |
| L5 字号上下限魔法数 | ✅ 已修 | theme.BODY_SIZE_MIN/MAX 三处统一 |
| L6 闹钟菜单分隔线 | ✅ 已修 | 删除提醒前 add_separator |
| L7 色卡键盘操作 | ✅ 已修 | takefocus=1 + Return/Space + 焦点高亮 |
| L8 空列表引导 | ✅ 已修 | 区分搜索无果/空文件夹的提示文案 |
| L9 缓存无上限 | ⏭ 跳过 | _PHOTO_CACHE 清领会破坏 PhotoImage 保活（项目红线）；media/cache 需 URL 反查，风险>收益 |
| L10 提醒状态悬空键 | ✅ 已修 | _alarm_prune_keys 兜底清理（回收站快照 id 视为存活，清空后才清） |
| L11 快照同秒覆盖 | ✅ 已修 | 文件名加毫秒后缀 |

---

## 🔴 高优先级（建议优先处理）

### H1. 加密笔记的图片会被静默漏出备份 — 静默数据丢失
- **位置**：`scripts/backup.py:385-394` `pack_media()` + `scripts/app_actions.py:5486`
- **问题**：加密笔记锁定后 `n["content"] = []`，图片引用只存在于密文 `sec` 里。`pack_media()` 只从 `content` 用 `collect_refs()` 收集 img 引用，因此**加密笔记里的图片不进备份**。换机导入后即使解锁，图片指向的 `media/<hash>.png` 也不存在 → 永久断图，且无任何提示。
- **建议**：导出时改为扫描整个 `data/media/` 目录打包全部 PNG（不依赖笔记锁定状态）；或导出前强制要求解锁所有含图加密笔记。
- **核验**：已确认 `5486` 行锁定后清空 content，`collect_refs()` 只读 content。
- **处理**：`pack_media()` 改为直接扫 `media/` 根目录全部合法 PNG（排除 `cache/`），与 `collect_refs` 取并集；回归 `tests/test_backup.py::MediaBackupTest`（无正文引用的图也打包 / 往返还原 / 跳过 cache）。38 例通过。

---

## 🟠 中优先级

### M1. 设置窗「取消」不回滚主题 / 字体 — 交互语义不一致
- **位置**：`scripts/app_ui.py:4994-4995` `cancel()`、`:4441-4449` `on_family()`、`:4661-4685` `on_theme()`
- **问题**：主题色卡与字体族在点选当下就 `set_theme()` / `save_data()` 落库；同窗的字号、多标签、工具栏、PicList 路径却是点「确定」才落库。用户点「取消」期望全部还原，主题/字体却已经变了。
- **建议**：进窗时快照 theme/font_family，`cancel()` 里若变化则回滚并 `save_data()`；或统一为「确定」才落库。
- **核验**：`on_theme()` 经 `set_theme()` 即时落库、`cancel()` 只 `dlg.destroy()`。

### M2. 查找/替换浮条换肤后悬停闪回旧主题色
- **位置**：`scripts/app_ui.py:2273-2276`（闭包捕获 `:2248` 的 `p`）、`:2678` `_restyle_find_bar()`
- **问题**：按钮 `<Enter>/<Leave>` 悬停回调闭包引用建窗时的 `p = self.pal`；`set_theme()` 换成新调色板对象后，`_restyle_find_bar()` 只重配静态 bg/fg，未重绑悬停回调 → 换肤后悬停这几枚按钮会闪回旧主题色。
- **建议**：悬停色改为运行时取 `self.pal`（`_hover_bg(self.pal["editor"])`），或在 `_restyle_find_bar()` 里重绑事件。
- **核验**：`_restyle_find_bar()` 确认只 configure 静态色、无 bind。

### M3. 搜索结果顺序与主列表排序口径不一致
- **位置**：`scripts/app_ui.py:390-393` `visible_notes()` + `scripts/store.py:1682-1683`
- **问题**：主视图走 `note_sort_key`（置顶 > 手动 order > 星标 > 时间），但搜索时直接返回 `search_ids()` 结果，SQL 按 `pinned/starred/updated` 排——忽略手动拖拽的 `order`，且把星标提到手动顺序之前。`refresh_notes()` 直接用这个顺序，用户可见跳变。
- **建议**：搜索结果回 Python 侧用 `note_sort_key` 重排，或让 SQL 排序键与之一致。

### M4. 密文 `iter` 无上限校验 — 恶意备份可致解锁卡死（DoS）
- **位置**：`scripts/secret.py:151-159, 366-397`
- **问题**：`open_sealed()` / `open_recovery()` 直接把 `blob["iter"]` 喂给 `pbkdf2_hmac`，无范围检查；而备份容器层 `backup.py:543-547` 专门有 `_MAX_ITER` 防 DoS。损坏/恶意的备份把 iter 改成极大值，解锁会卡死数分钟。
- **建议**：与 backup 一致，解密前校验 iter 在合理区间（如 10 万~500 万），越界抛 `BadPassword`。

### M5. 性能：几处重复的 O(n²) / 全库扫描
- **位置与建议**：
  - `app_ui.py:6170-6183` `_refresh_folder_counts`：每文件夹各调一次 `folder_notes`（每次 subtree + 遍历全库），O(F×N)，被 7 处调用 → **一次遍历按 folder 分桶、累加子节点计数到父节点**。
  - `app_actions.py:5003-5064` `_alarm_check`：每 20 秒遍历全库全部 content，汇总表打开时每轮都重建正文 → **缓存提醒条目、仅在相关状态翻转时重建汇总表**。
  - `app_actions.py:1172-1183 + 3746-3764` `_flush_save`：每次落库都无条件 `_img_gc`（扫全库 content + media 目录），多数情况 removed 为空 → **加"可能孤儿化"脏标记，只在删笔记/删图块/上传成功/清回收站时触发**。
  - `app_ui.py:1020-1034` 搜索每按键整列刷新 + 最多 2000 次 `text.search` 高亮 → **加约 150ms 去抖**。
- **说明**：不会崩溃，但库变大后先表现为卡顿；M5 三项收益最大。

### M6. 异步延时回调未登记，关窗后可能抛 TclError
- **位置**：`scripts/app_ui.py:7526-7527, 6120-6121`
- **问题**：两处列表刷新直接用 `canvas.after(30/120, ...)`，不经 `self._after` 登记，也无 `_alive`/`winfo_exists()` 检查；关窗后 30~120ms 内回调访问已销毁画布会抛 TclError。
- **建议**：改用 `self._after` 登记以便关窗统一取消，或回调开头检查 `_alive` 与 `winfo_exists()`。

### M7. 导出文件名未过滤 Windows 保留设备名
- **位置**：`scripts/exporter.py:164-172` `safe_filename()`
- **问题**：只过滤 `\ / : * ? " < > |` 和控制字符，未处理 `CON/PRN/AUX/NUL/COM1-9/LPT1-9` 及结尾的点。标题为 `CON` 的笔记导出 `CON.md` 在 Windows 上会失败或被重定向到设备。
- **建议**：命中保留名加安全前缀（如 `_CON.md`）或尾部下划线，清理结尾点/空格。

### M8. 备份解压 / 远程图下载无大小上限
- **位置**：`scripts/backup.py:660-688, 616-652`（gzip 炸弹）；`scripts/imgstore.py:244-249, 252-314`（远程图）
- **问题**：`f.read()` / `gzip.decompress()` 无解压后大小限制，恶意 `.json.gz` 导入可瞬间撑满内存，且在 checksum 校验之前；远程图 `resp.read()` 无 `Content-Length` 上限，失控 URL 可耗尽内存。
- **建议**：流式解压 + 字节数超阈值抛 `BackupError`；下载设最大字节数、流式计数超限中止。

### M9. 更新包 digest 缺失时静默跳过完整性校验
- **位置**：`scripts/updater.py:382-410` `prepare()`
- **问题**：远端 asset 无合法 `digest` 时 `if want:` 整块被跳过，下载包不经 sha256 直接 `extractall()`——"看似校验、实则未校验"。
- **建议**：digest 缺失时拒绝安装或弹明确二次确认。

---

## 🟡 低优先级 / 锦上添花

### L1. `_ask_password()` 空口令点确定静默无反应
- `scripts/app_ui.py:11101-11103`：空口令直接 `return`，窗口不关、无提示；同类 `_ask_link()` 有内联 hint。解锁加密笔记时易以为程序卡死。
- 建议：加「口令不能为空」hint 并聚焦输入框。

### L2. 死代码 / 未使用导入
- `scripts/app_ui.py:4847` `pick_exe()` 定义后全库零引用——PicList 路径 Entry 旁只有「测试上传」，没有「浏览」按钮，只能手输路径。倾向补一个浏览按钮接线。
- `scripts/app_ui.py:15` `from tkinter import ttk` 未使用（仅注释提及历史实现），可删。
- `scripts/store.py:658` `ALARM_TASK_COL = ALARM_COLS[1]` 注释称"留作兼容"但零引用，确认后删或写清兼容对象。

### L3. 昨天判断跨年边界（off-by-one）
- `scripts/notes_app.py:539`：用 `tm_yday` 差 1 且同年判断，1 月 1 日看去年 12 月 31 日笔记会显示日期而非「昨天」。改用 `date` 对象做差。

### L4. 像素截断三份实现、阈值口径不一
- `widgets.py:2822` / `mention_ui.py:532` / `app_ui.py:_ellipsize`：三份近似实现；阈值一个写死 `8`、一个用 `dpi.px(8)`。抽共用工具并统一走 dpi。
- `store.py:980, 1004` `_disp_w()` 与 `_clip_cell()` 的全角/省略号宽度公式重复，抽 `_disp_w_of_char()`。

### L5. 正文字号上下限魔法数字散落
- `notes_app.py:559`、`app_ui.py:4337, 4459`：`min(25,max(8,...))` 重复实现，预览又硬编码 `25`。定义 `BODY_SIZE_MIN/MAX` 常量统一引用。

### L6. 闹钟卡片右键菜单缺分隔线
- `app_actions.py:4907-4915`：「删除提醒」危险项前无 `add_separator()`，而笔记/文件夹/回收站菜单的危险项前都有。补齐保持一致。

### L7. 主题色卡无键盘操作入口
- `app_ui.py:4692-4700`：6 张色卡 `takefocus=0`，只绑鼠标事件；同窗复选框/滑杆都可键盘操作。让色卡 `takefocus=1` 并绑 `<Return>/<space>`。

### L8. 空列表无引导文案
- `app_ui.py:7484-7516`：可见笔记为空时列表区无任何提示，新建笔记入口只藏在右键菜单。加浅色引导「这里还没有笔记 · 右键空白处或按 Ctrl+N 新建」，搜索无果用「没有匹配的笔记」区分。

### L9. 缓存无上限
- `widgets.py:3533, 3598-3617` `_PHOTO_CACHE` 只增不清（key 含配色，换肤攒旧条目）→ 换肤时清理或改 LRU。
- `app_actions.py:3800-3836` `media/cache` 图床图按 URL hash 只写不清理 → 纳入 `_img_gc` 或做过期清理。

### L10. 删除笔记未级联清理提醒状态
- `app_actions.py:5603, 6076-6081` + `4969-4986`：删除只进回收站，未清 `settings["alarm_fired"]/["alarm_acked"]` 中属于该笔记的键；`_alarm_prune_keys()` 只按日期清重复键。功能上因 id 随机基本不误触发，但存在悬空状态。删除/还原时按 note_id 前缀同步清理。

### L11. 启动同步做重活 / 同秒快照覆盖
- `notes_app.py:71-108`：建 UI 前同步 load_data + 多个幂等检查各自 save_data + recycle_purge + `_img_gc` 全库扫描，大库启动卡顿 → 合并写库为一次、`_img_gc` 挪到 `_after_idle`。
- `store.py:1770-1790`：兜底快照 `pending_%Y%m%d-%H%M%S.json` 同秒多次会覆盖 → 加毫秒/短随机后缀。

---

## 已核查为「一致 / 合理设计」，无需处理
- 字体字号：全项目仅导入弹窗一处不一致（已于 11-20 修复）；图片与图床、数据管理、关于、恢复码窗、图片浮层、任务提醒浮层等内部自洽。
- 主题派生、24 处换肤覆盖、关闭保存双保险、图片后台下载/更新回调线程模型、搜索走 SQLite、写库按签名增量——基础设施扎实。
- 项目红线（行号=格序号、自动换行 ↵→CHAR、只读不设 DISABLED、备份两层 gzip、ALARM_INDEX_ID 固定 id、彻底删除入口已取消、回收站清空后 GC 等）均为有意设计，未列入问题。

## 环境提醒（非代码问题）
- `data/notes_data.db` 当前 notes 表为空 → `test_settings_dialog` 里依赖"可编辑普通笔记"的 alarm/shift_tab/undo 用例会失败（约 10 例）。这是测试数据依赖，非功能缺陷；未擅自改动你的真实库。
