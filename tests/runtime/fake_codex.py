#!/usr/bin/env python3
"""Docker protocol fixture: synthetic credentials, no inference or network calls."""
import errno
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    if os.environ["CRC_TEST_PROBE_MODE"] != "finished":
        time.sleep(120)
    print("codex-cli synthetic")
    sys.exit(0)

assert "--ephemeral" in sys.argv and "read-only" in sys.argv
auth = json.loads(Path("/codex-home/auth.json").read_text())
assert auth["synthetic"] is True
assert Path("/input/schema.json").is_file()
assert {p.name for p in Path("/workspace").iterdir()} == {"base", "head"}
assert not Path("/grading/keys.json").exists()
try:
    Path("/workspace/head/a.py").write_text("mutated")
except OSError as error:
    assert error.errno == errno.EROFS, "Pinned workspace must use a read-only mount"
else:
    raise AssertionError("Pinned workspace is writable")

prompt = sys.stdin.read()
assert "OWNED CHANGED HUNKS" in prompt, "Docker must forward the task prompt on stdin"
print(json.dumps({"type": "runtime.fake.started", "synthetic": True}), flush=True)
if auth["mode"] in {"timeout", "interrupted"}:
    time.sleep(120)
if auth["mode"] == "failed":
    sys.exit(23)

result = {"kind": "calibration-result", "version": 1, "status": "complete",
          "inspected_hunks": re.findall(r"^HUNK ([0-9a-f]+)$", prompt, flags=re.M),
          "extra_visible_bytes": 0, "findings": [], "gaps": []}
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}), flush=True)
