"""LogoBuilder GUI 包（tkinter + ttkbootstrap）。

模块划分：
* :mod:`logobuilder.ui.main` —— 命令行入口（创建 root / 模式分发）
* :mod:`logobuilder.ui.app` —— 主窗口与全部用户操作流程
* :mod:`logobuilder.ui.theme` —— 主题、调色板、字体、高 DPI
* :mod:`logobuilder.ui.session` —— UI 侧数据模型（唯一直接使用 core 的模块）
* :mod:`logobuilder.ui.workers` —— 后台任务 / 进度 / 取消
* :mod:`logobuilder.ui.blocklist` / :mod:`~logobuilder.ui.preview` —— 左右主面板
* :mod:`logobuilder.ui.dialogs` —— 分辨率 / 替换图片 / 错误 / 设置 / 帮助等对话框
* :mod:`logobuilder.ui.screenshot` —— 截图（含超屏拼接）
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "2.0.0"
