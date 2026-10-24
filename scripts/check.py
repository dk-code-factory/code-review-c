#!/usr/bin/env python3
"""Validate self-contained skill packaging without inference or dependencies."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "codex/skills/code-review-c"


def check():
    plugin = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
    assert plugin["name"] == "code-review-c" and re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])
    assert plugin["skills"] == "./codex/skills/"
    assert list((ROOT / "codex/skills").glob("*/SKILL.md")) == [SKILL / "SKILL.md"]
    entry = (SKILL / "SKILL.md").read_text()
    assert entry.startswith("---\nname: code-review-c\ndescription: ")
    assert "TODO" not in entry and "[TODO" not in entry
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    assert [p["name"] for p in marketplace["plugins"]] == [plugin["name"]]
    assert "allow_implicit_invocation: false" not in (SKILL / "agents/openai.yaml").read_text()
    forbidden = {"campaigns", "profiles", "private", "node_modules", ".env", "auth.json", "config.toml"}
    for file in ROOT.rglob("*"):
        relative = file.relative_to(ROOT)
        if any(p in {".git", "__pycache__", ".pytest_cache", ".venv"} for p in relative.parts):
            continue
        assert not forbidden & set(relative.parts), f"private artifact: {relative}"
        assert file.suffix != ".jsonl", f"trace in public package: {relative}"
        assert not file.is_symlink(), f"non-self-contained symlink: {relative}"
        if not file.is_file():
            continue
        if file.suffix == ".json":
            json.loads(file.read_text())
        if file.suffix == ".py":
            compile(file.read_bytes(), str(file), "exec")
            if file.is_relative_to(SKILL / "scripts"):
                for node in ast.walk(ast.parse(file.read_text())):
                    if isinstance(node, ast.Import):
                        modules = [n.name.split(".")[0] for n in node.names]
                    elif isinstance(node, ast.ImportFrom) and not node.level:
                        modules = [(node.module or "").split(".")[0]]
                    else:
                        continue
                    assert all(m in sys.stdlib_module_names or m == "reviewlib" for m in modules), modules
        if file.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", file.read_text()):
                if "://" not in link and not link.startswith("#"):
                    target = (file.parent / link.split("#")[0]).resolve()
                    assert target.is_relative_to(ROOT) and target.exists(), (relative, link)
    provenance = json.loads((ROOT / "PROVENANCE.json").read_text())
    assert provenance["version"] == plugin["version"]
    for source in provenance["sources"]:
        assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in source["sourceHashes"].values())
    assert "MIT License" in (ROOT / "LICENSE").read_text()
    assert provenance["sources"][0]["commit"] in (ROOT / "NOTICE").read_text()
    subprocess.run([sys.executable, str(SKILL / "scripts/review.py"), "--help"], check=True, stdout=subprocess.DEVNULL)
    print("Skill discovery, links, provenance, syntax, and public-package checks passed")


if __name__ == "__main__":
    check()
