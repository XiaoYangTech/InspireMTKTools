# -*- coding: utf-8 -*-
"""Fetch a GitHub git blob by SHA (works for >1MB files where contents API returns encoding=none).

Writes bytes to output path and prints sha256. Local analysis helper only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import urllib.request

API = "https://api.github.com/repos/{repo}/git/blobs/{sha}"


def main() -> int:
    repo, sha, out = sys.argv[1], sys.argv[2], sys.argv[3]
    req = urllib.request.Request(
        API.format(repo=repo, sha=sha),
        headers={"User-Agent": "inspire-mtk-research", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
    obj = json.loads(raw)
    data = base64.b64decode(obj["content"])
    print(f"declared size={obj.get('size')} decoded={len(data)} encoding={obj.get('encoding')}")
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"WROTE {out} sha256={hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
