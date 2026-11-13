# -*- coding: utf-8 -*-
"""Create (or verify) the public CNB repository and print its URLs.

Token comes from the local git credential helper (never printed/logged).
CNB OpenAPI: https://api.cnb.cool  (docs: docs.cnb.cool)
  POST /{group_slug}/-/repos   body {name, description, license, visibility}
  Accept: application/vnd.cnb.api+json ; Authorization: Bearer <token>
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

GROUP = "XiaoYangTech"
NAME = "InspireMTKTools"
DESCRIPTION = "亿方联发科工具箱 InspireMTKTools — MTK 开机 Logo 编辑器 + LK 引导程序修补"
TOKEN_FILE = Path(tempfile.gettempdir()) / "inspire_publish" / "cnb_token.txt"
BASE = "https://api.cnb.cool"
ACCEPT = "application/vnd.cnb.api+json"


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
    os.chmod(TOKEN_FILE, 0o600)
    return token


def api(url: str, token: str, *, method: str = "GET", payload: dict | None = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "User-Agent": "InspireMTKTools-publish",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
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
    print("token_len:", len(token))

    # identity
    status, me = api(f"{BASE}/user", token)
    print("GET /user ->", status, str(me)[:200])

    status, repo = api(f"{BASE}/{GROUP}/-/repos/{NAME}", token)
    print("lookup ->", status, str(repo)[:300])
    if status == 200:
        print("exists")
        return 0

    status, created = api(
        f"{BASE}/{GROUP}/-/repos",
        token,
        method="POST",
        payload={
            "name": NAME,
            "description": DESCRIPTION,
            "license": "MIT",
            "visibility": "public",
        },
    )
    print("create ->", status)
    print(json.dumps(created, ensure_ascii=False)[:1200])
    return 0 if status in (200, 201) else 1


if __name__ == "__main__":
    raise SystemExit(main())
