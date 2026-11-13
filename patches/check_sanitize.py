# -*- coding: utf-8 -*-
"""Assert the patched gateway rewrites the REAL DSH prompt lines (not a made-up sample)."""
import json
import pathlib
import sys

import zstandard

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, ROOT)
import wb_proxy  # noqa: E402

# 真实原文（从 DSH 会话文件里取，不是手写的）
src = sorted((pathlib.Path.home() / ".dsh" / "sessions").rglob("session.v4.jsonl.zstd"),
             key=lambda p: p.stat().st_mtime, reverse=True)[0]
raw = zstandard.ZstdDecompressor().stream_reader(open(src, "rb")).read().decode("utf-8", "replace")
sys_text = ""
for line in raw.splitlines():
    try:
        rec = json.loads(line)
    except Exception:
        continue
    if rec.get("type") == "system/message":
        parts = (rec.get("data", {}).get("message") or {}).get("content") or []
        sys_text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        break

out = wb_proxy.sanitize_dsh_prompt(sys_text)
print("系统提示长度:", len(sys_text))
print("有改写吗:", out != sys_text)
print("原文含 'are paths the user explicitly referenced':",
      "are paths the user explicitly referenced" in sys_text)
print("改写后还含吗:", "are paths the user explicitly referenced" in out)
print("改写后含 'point at paths the user named':", "point at paths the user named" in out)
print()
if out == sys_text:
    print("结论: 没生效 —— guard 又写错了")
    sys.exit(1)
if "are paths the user explicitly referenced" in out:
    print("结论: 没替换干净")
    sys.exit(1)
print("结论: OK，真实提示词会被改写")
