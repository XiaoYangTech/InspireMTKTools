# -*- coding: utf-8 -*-
"""Create the CNB release v3.0.0 and upload the Windows exe asset.

CNB OpenAPI flow:
  1. POST /{repo}/-/releases                       -> release.id
  2. POST /{repo}/-/releases/{id}/asset-upload-url -> {upload_url, verify_url}
  3. PUT  upload_url (presigned object storage URL, raw bytes)
  4. POST verify_url (confirm asset)
  5. GET  /{repo}/-/releases/tags/v3.0.0           -> verify assets

Token is read from the local git credential helper (never printed).
Idempotent: existing release/asset are reused/replaced when --replace is given.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.cnb.cool"
REPO = "InspireWorks/InspireMTKTools"
TAG = "v3.0.0"
EXE = Path("dist/InspireMTKTools.exe")
TOKEN_FILE = Path(tempfile.gettempdir()) / "inspire_publish" / "cnb_token.txt"
ACCEPT = "application/vnd.cnb.api+json"

BODY = """# 亿方联发科工具箱 InspireMTKTools v3.0.0

联发科（MTK）Android 工具箱：**开机 Logo 编辑器** + **LK 引导程序修补器**。

## 功能
- **开机 Logo**：`logo.bin` / `logo.img` 解析与打包（RGB565 / BGRA8888），支持 240×320 等竖屏小尺寸帧；
  未修改块逐字节保留，真实样本 `extract → pack --pad-to` 回包 SHA-256 一致。
- **LK 修补**：去除 Orange/Red State 开机警告与 5 秒等待、去除 dm-verity 警告与等待（约 3 秒）；
  ARM32 特征码 + AArch64 启发式；自动备份、补丁前后校验、内核启动参数保护、fail-safe。
- **界面**：ttkbootstrap `bootstrap-light` 单一浅色主题、全中文单语、高分屏自适应（100%–200%）。

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
    payload = b"protocol=https\nhost=cnb.cool\n\n"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    out = subprocess.run(["git", "credential", "fill"], input=payload, capture_output=True,
                         timeout=30, env=env).stdout.decode("utf-8", "replace")
    token = ""
    for line in out.splitlines():
        if line.startswith("password="):
            token = line.split("=", 1)[1]
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


TOKEN = ""


def api(url: str, *, method: str = "GET", payload: dict | None = None):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": ACCEPT,
        "User-Agent": "InspireMTKTools-cnb-release",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    last: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试
            last = exc
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"network error: {last}")


def upload_to_url(url: str, data: bytes) -> int:
    """PUT bytes to a presigned upload URL (no Bearer token sent)."""
    headers = {"Content-Type": "application/octet-stream", "Content-Length": str(len(data))}
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                resp.read()
                return resp.status
        except urllib.error.HTTPError as exc:
            print("  upload HTTP error:", exc.code, exc.read()[:300])
            return exc.code
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"upload network error: {last}")


def main() -> int:
    global TOKEN
    TOKEN = get_token()
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    print("exe:", EXE, EXE.stat().st_size, "bytes | target commit:", head)

    # 1) existing release?
    status, release = api(f"{BASE}/{REPO}/-/releases/tags/{TAG}")
    if status == 200 and isinstance(release, dict) and release.get("id"):
        print("release exists:", release.get("id"), release.get("tag_name"))
    else:
        status, release = api(
            f"{BASE}/{REPO}/-/releases",
            method="POST",
            payload={
                "tag_name": TAG,
                "target_commitish": head,
                "name": f"亿方联发科工具箱 InspireMTKTools {TAG}",
                "body": BODY,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            },
        )
        print("create release ->", status)
        if status not in (200, 201) or not isinstance(release, dict) or not release.get("id"):
            print("CREATE_FAILED:", str(release)[:800])
            return 1
        print("release:", release.get("id"), release.get("tag_name"))

    release_id = str(release["id"])
    assets = release.get("assets") or []
    if any(a.get("name") == EXE.name for a in assets):
        print("asset exists; removing old one for replacement")
        for asset in assets:
            if asset.get("name") == EXE.name:
                status, _ = api(f"{BASE}/{REPO}/-/releases/{release_id}/assets/{asset['id']}", method="DELETE")
                print("delete old asset ->", status)

    # 2) presigned upload URL
    status, info = api(
        f"{BASE}/{REPO}/-/releases/{release_id}/asset-upload-url",
        method="POST",
        payload={"asset_name": EXE.name, "size": EXE.stat().st_size, "overwrite": True},
    )
    print("asset-upload-url ->", status)
    if status not in (200, 201) or not isinstance(info, dict) or not info.get("upload_url"):
        print("UPLOAD_URL_FAILED:", str(info)[:800])
        return 1
    upload_url = info["upload_url"]
    verify_url = info.get("verify_url") or ""
    print("upload_url:", upload_url[:120], "...")
    print("verify_url:", verify_url[:120], "...")

    # 3) upload bytes
    code = upload_to_url(upload_url, EXE.read_bytes())
    print("upload put ->", code)
    if code not in (200, 201, 204):
        return 1

    # 4) confirm upload
    if verify_url:
        status, result = api(verify_url, method="POST", payload={})
        print("verify ->", status, str(result)[:300])
        if status not in (200, 201):
            return 1

    # 5) verify release assets
    status, final = api(f"{BASE}/{REPO}/-/releases/tags/{TAG}")
    assets = final.get("assets") if isinstance(final, dict) else None
    print("final release:", status, final.get("name") if isinstance(final, dict) else "")
    print("assets:", [(a.get("name"), a.get("size"), a.get("browser_download_url")) for a in assets or []])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
