# 亿方联发科工具箱 / InspireMTKTools v3.0.0 — UI 说明（gui-dev 交付）

> 入口：`python app.py`（GUI）/ `python app.py --self-test` / `python app.py --screenshot out.png`
> 技术栈：Python 3.10+、tkinter + ttkbootstrap 2.x（**bootstrap-light** 主题）、Pillow；核心库 `logobuilder.core`
> （内部 Python 包名仍为 `logobuilder`，仅用户可见文案改为新品牌，避免破坏既有 API/测试）。
> 产品：**亿方联发科工具箱 / InspireMTKTools v3.0.0** — MediaTek 开机 Logo 编辑 + LK 引导程序修补。
> 界面语言：**仅中文**；主题：**仅 bootstrap-light 浅色**（无深色模式、无语言切换）。

## 0. bootstrap-light 视觉规范

| 元素 | 取值 |
|---|---|
| 页面 / 卡片 / 面板底色 | `#ffffff`（白卡片 + 1px 细边框 `#dee2e6`） |
| 正文文字 | `#343a40`；次要文字 `#6c757d` |
| 主色（高亮/选中/主按钮） | `#4582ec` |
| 成功 / 警告 / 危险 | `#02b875` / `#f0ad4e` / `#d9534f` |
| 次浅色底 | `#f8f9fa`；悬停 `#f1f3f5` |
| 顶栏 | 白底 + 底部 1px `#dee2e6` 分隔线；品牌图标为 primary 蓝底白字圆角块 |
| 样式来源 | ttkbootstrap `bootstrap-light` 基座主题（2.x 新版内置主题）+ 自绘控件统一使用 bootstrap-light 调色板 |

## 1. 界面结构（两个工作区）

顶栏：`亿方联发科工具箱` / `InspireMTKTools v3.0.0` 品牌 + 右侧 `设置` / `帮助`（无深色、无英文切换）；
工具栏：打开镜像、新建项目、打开项目 │ 替换图片、恢复原图 │ 导出 logo.img、打包 logo.bin、生成卡刷包、第二屏 │ **LK 修补**（全部 bootstrap-light bootstyle：outline-primary / primary / outline-secondary）。

### Tab 1：开机 Logo（原有工作区，行为不变）

```
┌ 块列表（缩略图卡片/序号/尺寸/类型/状态；过滤框） ┬ 预览（适应窗口/1:1、棋盘格、信息卡） ┐
├ 可折叠日志面板（INFO/WARNING/ERROR、复制/保存/清空，白底+细边框）                      ┤
└ 状态栏（状态消息 · 来源/块数/文件大小 · 进度/取消 · 显示日志，白底+顶部细线）            ┘
```
* 支持打开 `logo.bin/logo.img`、新建/打开项目、替换图片（拉伸/等比留白/等比裁剪 + 实时转换预览）、恢复原图、导出 logo.img（0x00/0xFF 填充）、打包 logo.bin、生成 update.zip、第二屏 boot_logo。
* 分辨率未知时弹出候选列表（置信度 + 中文判定依据 + 实时预览 + 自定义 WxHxD + 应用到同尺寸块 + 记住选择）。
* 卡片选中 = 浅蓝底 + primary 描边；悬停 = `#f1f3f5`；状态徽章按 bootstrap-light success/warning/danger。

### Tab 2：LK 修补

```
┌ 标题 + 核心状态徽章 + 风险提示（右上）                                                ┐
├ 添加 LK 文件… / 选择文件夹… / 开始扫描 / 清空列表                      刷写说明…   ┤
├──────────── 左：LK 镜像表（文件/大小/架构/结论/SHA-256，primary 表头） ─┬─ 右：扫描详情 ─┤
│  · A/B 双槽提醒（lk_a/lk_b、lk/lk2 都需刷）                            │ 文件信息卡       │
│                                                                        │ 候选补丁表       │
│                                                                        │ 警告文本/内核参数 │
├ 修补选项：☑橙色/红色警告+等待 ☑dm-verity 警告+等待 ☐同时清空警告文本   │ 输出：新文件/覆盖 ┤
├ 预览补丁 / 应用补丁 / 校验结果 / 还原备份…（贴底固定，小窗口不裁切）                  ┤
└ 状态栏（LK 修补 · N 个镜像）                                                         ┘
```

* 支持 MTK LK 分区镜像（512B 头）与裸 payload；AArch64 启发式识别 + ARM32 特征码规则；fail-safe：无特征/校验失败不写文件。
* **预览补丁**（dry-run）：变更列表 + 补丁前后反汇编 + 差异区间，不写盘。
* **应用补丁**：后置校验（大小/头部 0x200/内核参数计数/差异区间/补丁点反汇编）通过才写盘；默认生成 `_patched` 新文件，覆盖模式自动生成 `_original_backup` 备份。
* **校验结果**：PASS/FAIL + 各项校验值 + 补丁点反汇编 + 残留警告串数量。
* **还原备份**：选择备份文件与目标文件后还原，并重新扫描。
* **刷写说明**：危险提示、fastboot 命令（按 lk/lk_a/lk_b/lk2 槽位生成）、SP Flash Tool（Download Only）、A/B 双槽提醒、seccfg/mtkclient 前置条件、AVB/dm-verity 边界说明；一键复制命令。
* 扫描/应用/校验/还原全部后台线程执行，进度与结果进主窗口日志面板；错误统一走中文错误对话框（可展开/复制/保存 traceback）。

## 2. 快捷键

| 快捷键 | 功能 | 快捷键 | 功能 |
|---|---|---|---|
| Ctrl+O | 打开 logo.bin / logo.img | Ctrl+R | 替换图片 |
| Ctrl+N | 从镜像新建项目 | Ctrl+Z | 恢复原图 |
| Ctrl+Shift+O | 打开项目目录 | Ctrl+S | 保存项目 |
| Ctrl+E | 导出 logo.img | Ctrl+B | 打包 logo.bin |
| Ctrl+U | 生成 update.zip | Ctrl+K | 切换到 LK 修补 |
| Ctrl+L | 显示/隐藏日志 | Ctrl+, | 设置 |
| F5 | 刷新预览 | F1 | 帮助 |

## 3. HiDPI 与布局

* `app.py` 在创建 Tk root **之前** 调 `enable_high_dpi()`（Windows Per-Monitor V2 → SetProcessDpiAwareness(2) → SetProcessDPIAware 逐级回退；非 Windows 自动跳过）。
* 缩放 = `winfo_fpixels("1i")/96`，设置 `tk scaling = scale × 96/72`；字体用磅值，所有间距/图标/卡片/对话框尺寸经 `Metrics.px()` 换算；布局只用 grid/pack，工具栏按宽度自动折行，顶栏窄屏隐藏副标题。
* 窗口大小/位置、Paned 分割位置、当前工作区（Tab）记入 `ui_settings.json`；小屏（宽 < 1000）自动最大化。
* 单一 bootstrap-light 主题，无深浅切换；主题/语言配置项已从 `UiSettings` 删除（旧配置文件中的旧字段读取时忽略）。
* 测试：`tests/test_ui/test_hidpi.py` 用 `tk scaling` 1.0 / 1.5 校验关键控件无裁切；150% 截图 1860×1230 正常。

## 4. 测试与验收命令

```bash
python app.py                  # 启动 GUI（中文、bootstrap-light 浅色）
python app.py --self-test      # 15 项自检：Logo 全流程 + 两工作区 + 单一 bootstrap-light/中文断言 + LK 全流程
python app.py --screenshot analysis/ui_screenshots/main.png   # 生成 12 张截图
python -X utf8 -m pytest tests/test_ui -q
python -X utf8 -m pytest tests -q
```

自检/截图退出码 0；`app.py` / `ui/main.py` 在 Windows GBK 控制台下把 stdout/stderr 重配置为 UTF-8。

## 5. 截图（`analysis/ui_screenshots/`，12 张，全部 bootstrap-light 浅色）

| 文件 | 内容 |
|---|---|
| `main.png` / `main_window.png` | 主窗口（新品牌 + 两个 Tab + bootstrap-light 白卡片，演示 Logo 项目） |
| `settings_dialog.png` | 设置对话框（无主题/语言项） |
| `main_window_150.png` | 150% 缩放（1860×1230） |
| `resolution_dialog.png` | 分辨率选择对话框 |
| `replace_image_dialog.png` | 替换图片对话框 |
| `error_dialog.png` | 错误详情（可展开 traceback） |
| `help_dialog.png` | 帮助 |
| `lk_patch_scan.png` | LK 修补面板：真实 `lk_a.bin`/`lk_b.bin` 扫描结果、候选补丁表、A/B 提醒 |
| `lk_patch_preview.png` | 补丁预览（dry-run）：变更列表 + 补丁前后反汇编 |
| `lk_patch_verify.png` | 补丁校验结果（PASS + 各项校验值） |
| `lk_patch_flash_guide.png` | 刷写说明（fastboot / SP Flash Tool / A/B 双槽 / 前置条件） |

> 深色截图已按需求移除，不再生成 `main_window_dark.png`。
> 截图模式对真实样本只做**只读复制**到临时目录后扫描/打补丁，`analysis/lk/samples/**` 不被修改。

## 6. 代码结构

```
app.py                              入口（sys.path + UTF-8 控制台 + DPI + 模式分发）
src/logobuilder/ui/
  main.py                            CLI：--self-test / --screenshot / --smoke（无 --lang/--theme）
  app.py                             主窗口：品牌、菜单、工具栏、Notebook 两个工作区、Logo 流程、中文/bootstrap-light 固定配置
  lkpatch_panel.py                   LK 修补面板 + 预览/校验/刷写说明对话框（只调用 core.lkpatch）
  theme.py                           bootstrap-light 调色板 + 高 DPI/字体/ttk 样式（单一浅色主题，无深色分支）
  toolbar.py                         自适应折行工具栏（10 个 bootstrap-light bootstyle 按钮）
  widgets.py / blocklist.py / preview.py / dialogs.py / session.py / workers.py
  imaging.py / screenshot.py / screenshot_mode.py / self_test.py / demo.py / settings.py / dnd.py / core_bridge.py
src/logobuilder/i18n/                仅中文文案目录 + Translator（单语，无 en 段/语言切换）
tests/test_ui/                       conftest + 10 个测试文件（含 test_lkpatch_ui.py、stubs/fake_lkpatch.py 仅测试用）
```

## 7. 已知限制 / 遗留风险

1. 本机 RDP 638×394：正常启动会 auto-maximize；截图模式用 overrideredirect + 分屏拼接输出完整设计尺寸（1240×820，150% 为 1860×1230）。
2. `tkinterdnd2` 未安装（可选依赖）→ 拖放禁用并优雅降级；安装后自动启用。
3. macOS/Linux 未实机跑 GUI（无 Windows-only 硬依赖；DPI 函数仅 win32 生效）；真 4K/200% 屏未测，仅程序化模拟 100%/150%。
4. bootstrap-light 在 ttkbootstrap 2.x 中为 legacy Bootswatch 主题名（有 DeprecationWarning，已在启动处安静处理；3.0 升级时需改用 2.x 主题或自行补 bootstrap-light 样式）。
5. LK AArch64 补丁为启发式识别：不同厂商 LK 代码差异可能导致识别失败 → fail-safe 不写文件；只在 1 个真实样本（lk_a/lk_b）验证过，未在真机验证。
6. LK 阶段警告消失 ≠ 关闭全部校验：kernel/vbmeta 层 AVB/dm-verity 不受影响；A/B 双槽必须都刷；部分机型需 seccfg 解锁。
7. 超大镜像扫描/打包为单次 core 调用，进度条为不确定进度；取消为协作式（核心调用返回后丢弃结果）。
