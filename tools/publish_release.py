# -*- coding: utf-8 -*-
"""Create the GitHub release v3.0.0 and upload the Windows onefile exe asset.

Token is read from the local git credential helper (never printed).
Idempotent: existing release/asset are left untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "XiaoYangTech"
REPO = "InspireMTKTools"
TAG = "v3.0.0"
EXE = Path("dist/InspireMTKTools.exe")
TOKEN_FILE = Path(tempfile.gettempdir()) / "inspire_publish" / "gh_token.txt"

BODY = """# 亿方联发科工具箱 InspireMTKTools v3.0.0

联发科（MTK）Android 工具箱：**开机 Logo 编辑器** + **LK 引导程序修补器**。

## 功能
- **开机 Logo**：`logo.bin` / `logo.img` 解析与打包（RGB565 / BGRA8888），未修改块逐字节无损回包；
  预览 / 替换图片 / 分辨率自动识别（候选+置信度）/ 导出 `logo.img`（可填充分区）、`logo.bin`、
  CWM/TWRP 卡刷包 `update.zip`、第二屏 `boot_logo`。
- **LK 修补**：去除 Orange/Red State 开机警告与 5 秒等待、去除 dm-verity 警告与等待（约 3 秒）；
  ARM32（7 条特征码）+ AArch64（capstone 启发式定位警告分发函数/dm-verity 函数）；
  自动备份、补丁前后反汇编校验、内核启动参数保护、fail-safe、A/B 双槽提醒。
- **界面**：ttkbootstrap **litera** 单一浅色主题、全中文单语、高分屏自适应（100%–200%）。

## 使用
```bash
pip install -r requirements.txt
python app.py            # 图形界面
python -m logobuilder.core.cli -h
```
Windows 用户可直接下载下方 `InspireMTKTools.exe`（单文件，无需 Python）。

## 注意
- 修改 bootloader 有变砖风险，请先备份；A/B 机型两个槽位都要刷。
- 本工具只处理 LK 阶段的警告与等待，不等于关闭 AVB2/dm-verity 全部校验；未在真机验证。
- 详见仓库 README、docs/spec_lkpatch.md 与 docs/lk_patch_research.md。
"""


def get_token() -> str:
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    payload = b"protocol=https\nhost=github.com\n\n"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    out = subprocess.run(["git", "credential", "fill"], input=payload, capture_output=True,
                         timeout=30, env=env).stdout.decode("utf-8", "replace")
    token = ""
    for line in out.splitlines():
        if line.startswith("password="):
            token = line.split("=", 1)[1]
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return token


def api(url: str, token: str, *, method: str = "GET", payload: dict | None = None):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "InspireMTKTools-release",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def upload_asset(token: str, release_id: int, exe: Path) -> tuple[int, dict | str]:
    url = f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{release_id}/assets?name={exe.name}"
    data = exe.read_bytes()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(data)),
        "User-Agent": "InspireMTKTools-release",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def main() -> int:
    token = get_token()
    if not token:
        print("NO_TOKEN")
        return 2
    if not EXE.is_file():
        print("exe not found:", EXE)
        return 2
    print("exe:", EXE, EXE.stat().st_size, "bytes")

    base = f"https://api.github.com/repos/{OWNER}/{REPO}"
    status, release = api(f"{base}/releases/tags/{TAG}", token)
    if status == 200 and isinstance(release, dict):
        print("release exists:", release.get("html_url"))
    else:
        status, release = api(
            f"{base}/releases",
            token,
            method="POST",
            payload={
                "tag_name": TAG,
                "target_commitish": "main",
                "name": f"亿方联发科工具箱 InspireMTKTools {TAG}",
                "body": BODY,
                "draft": False,
                "prerelease": False,
            },
        )
        print("create release ->", status)
        if not isinstance(release, dict) or not release.get("id"):
            print("CREATE_FAILED:", str(release)[:800])
            return 1
        print("release:", release.get("html_url"))

    release_id = int(release["id"])
    status, assets = api(f"{base}/releases/{release_id}/assets", token)
    existing = {a.get("name") for a in assets} if isinstance(assets, list) else set()
    if EXE.name in existing:
        # 资产已存在：删除旧附件后重新上传（用于内容更新后替换）
        for asset in assets:
            if asset.get("name") == EXE.name:
                print("delete old asset", asset.get("size"))
                status, _ = api(f"{base}/releases/assets/{asset['id']}", token, method="DELETE")
                print("delete ->", status)
    status, result = upload_asset(token, release_id, EXE)
    print("upload ->", status)
    if isinstance(result, dict):
        print("asset:", result.get("name"), result.get("size"), result.get("browser_download_url"))
        return 0
    print("UPLOAD_FAILED:", str(result)[:800])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
