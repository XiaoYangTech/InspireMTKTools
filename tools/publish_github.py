# -*- coding: utf-8 -*-
"""Create (or verify) the public GitHub repository and print its URLs.

Token comes from the local git credential helper (never printed/logged).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

NAME = "InspireMTKTools"
DESCRIPTION = "亿方联发科工具箱 InspireMTKTools — MTK 开机 Logo 编辑器 + LK 引导程序修补（去 Orange/Red State 与 dm-verity 警告）"
TOKEN_FILE = Path(tempfile.gettempdir()) / "inspire_publish" / "gh_token.txt"


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
        "User-Agent": "InspireMTKTools-publish",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
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
    status, me = api("https://api.github.com/user", token)
    login = me.get("login") if isinstance(me, dict) else None
    print("login:", login)
    if not login:
        print("AUTH_FAILED", status, me)
        return 2

    status, repo = api(f"https://api.github.com/repos/{login}/{NAME}", token)
    if status == 200 and isinstance(repo, dict):
        print("exists:", repo.get("full_name"), repo.get("html_url"), "private=", repo.get("private"))
        return 0
    if status not in (404,):
        print("lookup status:", status, repo)

    status, created = api(
        "https://api.github.com/user/repos",
        token,
        method="POST",
        payload={
            "name": NAME,
            "description": DESCRIPTION,
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        },
    )
    print("create status:", status)
    if isinstance(created, dict) and created.get("full_name"):
        print("created:", created.get("full_name"))
        print("html_url:", created.get("html_url"))
        print("clone_url:", created.get("clone_url"))
        return 0
    print("CREATE_FAILED:", json.dumps(created, ensure_ascii=False)[:800])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
