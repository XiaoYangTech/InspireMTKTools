# -*- coding: utf-8 -*-
"""Probe locally stored git credentials (GCM) for GitHub / CNB and identify accounts.

Secrets are never printed: tokens are written to
``%TEMP%/inspire_publish/gh_token.txt`` and ``cnb_token.txt`` with chmod 600 for
the publish scripts.
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

OUT = Path(tempfile.gettempdir()) / "inspire_publish"
OUT.mkdir(parents=True, exist_ok=True)


def credential(host: str) -> tuple[str, str]:
    """Return (username, password) from git credential helper, or ("","")."""
    payload = f"protocol=https\nhost={host}\n\n".encode()
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input=payload,
            capture_output=True,
            timeout=30,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[{host}] credential fill failed: {exc}")
        return "", ""
    text = proc.stdout.decode("utf-8", "replace")
    user = pwd = ""
    for line in text.splitlines():
        if line.startswith("username="):
            user = line.split("=", 1)[1]
        elif line.startswith("password="):
            pwd = line.split("=", 1)[1]
    return user, pwd


def http_json(url: str, token: str, *, method: str = "GET", payload: dict | None = None,
              bearer: bool = False) -> tuple[int, dict | list | str]:
    headers = {
        "Accept": "application/vnd.github+json" if "github" in url else "application/json",
        "User-Agent": "InspireMTKTools-publish",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"token {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    results: dict = {}

    # ---- GitHub ----
    gh_user, gh_token = credential("github.com")
    print(f"[github] credential user={gh_user!r} token_len={len(gh_token)}")
    if gh_token:
        status, data = http_json("https://api.github.com/user", gh_token)
        print(f"[github] GET /user -> {status}")
        if isinstance(data, dict):
            print("  login:", data.get("login"), "| name:", data.get("name"), "| type:", data.get("type"))
            results["github"] = {"login": data.get("login"), "name": data.get("name")}
        else:
            print("  ", str(data)[:300])
        # scopes
        try:
            req = urllib.request.Request(
                "https://api.github.com/user",
                headers={"Authorization": f"token {gh_token}", "User-Agent": "probe"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                print("  scopes:", resp.headers.get("X-OAuth-Scopes"),
                      "| accepted-perms:", resp.headers.get("X-Accepted-GitHub-Permissions"))
        except Exception as exc:  # noqa: BLE001
            print("  scopes error:", exc)
        (OUT / "gh_token.txt").write_text(gh_token, encoding="utf-8")
        os.chmod(OUT / "gh_token.txt", 0o600)

    # ---- CNB ----
    cnb_user, cnb_token = credential("cnb.cool")
    print(f"[cnb] credential user={cnb_user!r} token_len={len(cnb_token)}")
    if cnb_token:
        (OUT / "cnb_token.txt").write_text(cnb_token, encoding="utf-8")
        os.chmod(OUT / "cnb_token.txt", 0o600)
        # CNB may expose a Gitea-compatible or custom API; probe a few endpoints
        for url in (
            "https://cnb.cool/api/v1/user",
            "https://api.cnb.cool/user",
            "https://cnb.cool/api/user",
        ):
            status, data = http_json(url, cnb_token, bearer=True)
            summary = data if not isinstance(data, dict) else {
                k: data.get(k) for k in ("login", "username", "name", "email", "id", "message")
            }
            print(f"[cnb] GET {url} -> {status} {str(summary)[:300]}")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
