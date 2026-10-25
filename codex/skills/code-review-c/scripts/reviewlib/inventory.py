"""Pin Git objects and expose a small inventory, never an initial full patch."""
import re
import subprocess
from pathlib import Path

from .records import digest, external, local_ref, load, record, save

LANGUAGES = {".py": "python", ".js": "javascript", ".jsx": "javascript",
             ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript",
             ".tsx": "typescript", ".java": "java", ".go": "go", ".cs": "csharp",
             ".php": "php", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
             ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp", ".rs": "rust"}
HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$", re.M)
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-relative",
                "--ignore-submodules=none", "--submodule=short", "--find-renames")


def git(repo, *args):
    return subprocess.check_output(["git", "--no-pager", "-C", str(repo), *args],
                                   stderr=subprocess.PIPE)


def revision(repo, value):
    result = git(repo, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", result):
        raise ValueError("invalid resolved Git object")
    return result


def pin_pr(repo, pr):
    meta = __import__("json").loads(subprocess.check_output([
        "gh", "pr", "view", str(pr), "--json",
        "number,url,title,body,baseRefOid,headRefOid,commits,files"], cwd=repo))
    # Fetch immutable object IDs from the base repository (GitHub retains PR objects).
    match = re.fullmatch(r"https://github.com/([\w.-]+/[\w.-]+)/pull/\d+", meta["url"])
    if not match:
        raise ValueError("unsupported PR URL")
    name = match[1]
    for oid in (meta["baseRefOid"], meta["headRefOid"]):
        if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise ValueError("invalid PR object ID")
        try:
            revision(repo, oid)
        except subprocess.CalledProcessError:
            git(repo, "fetch", "--no-tags", "https://github.com/" + name + ".git", oid)
    return meta


def build(repo, output, base=None, head=None, pr=None):
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    output = external(output, repo)
    if output.exists() and any(output.iterdir()):
        raise ValueError("inventory output must be new or empty")
    metadata = pin_pr(repo, pr) if pr else None
    if metadata:
        if base or head:
            raise ValueError("choose PR or explicit base/head")
        base, head = metadata["baseRefOid"], metadata["headRefOid"]
    if not base or not head:
        raise ValueError("explicit base and head revisions are required")
    base, head = revision(repo, base), revision(repo, head)
    diff_base = git(repo, "merge-base", base, head).decode().strip() if pr else base
    fields = git(repo, "diff", *DIFF_OPTIONS, "--raw", "--no-abbrev", "-z",
                 diff_base, head, "--").split(b"\0")
    files, hunks = [], []
    while fields and fields[0]:
        old_mode, new_mode, _, _, status = fields.pop(0).decode("ascii").split()
        old_mode = old_mode.removeprefix(":")
        old_raw = fields.pop(0)
        new_raw = fields.pop(0) if status[0] in "RC" else old_raw
        old, new = old_raw.decode("utf-8", "surrogateescape"), new_raw.decode("utf-8", "surrogateescape")
        paths = list(dict.fromkeys([old, new]))
        patch = git(repo, "diff", *DIFF_OPTIONS, "--binary", "--unified=3", diff_base, head, "--",
                    *[":(literal)" + p for p in paths])
        matches = list(HUNK.finditer(patch))
        header = patch[:matches[0].start()] if matches else b""
        units = [(header + patch[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(patch)], m)
                 for i, m in enumerate(matches)] or [(patch, None)]
        ids, added, removed = [], 0, 0
        for data, match in units:
            hid = digest({"path": new, "patch": digest(data)})[:24]
            ref = "hunks/" + hid + ".patch"
            target = output / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            delta = data[len(header):] if match else b""
            plus = sum(line.startswith(b"+") for line in delta.splitlines()[1:])
            minus = sum(line.startswith(b"-") for line in delta.splitlines()[1:])
            added += plus
            removed += minus
            span = None if not match else [int(match[1]), int(match[2] or b"1"),
                                           int(match[3]), int(match[4] or b"1")]
            hunks.append({"id": hid, "path": new, "old_path": old, "status": status,
                          "language": LANGUAGES.get(Path(new).suffix.lower(), "generic"),
                          "component": Path(new).parts[0] if len(Path(new).parts) > 1 else ".",
                          "bytes": len(data), "sha256": digest(data), "ref": ref,
                          "span": span, "binary": b"GIT binary patch\n" in data,
                          "opaque": status[0] == "T" or "160000" in {old_mode, new_mode}})
            ids.append(hid)
        files.append({"path": new, "old_path": old, "status": status, "added": added,
                      "removed": removed, "patch_bytes": len(patch), "hunks": ids})
    inv = record("inventory", repo=str(repo), base=base, head=head, diff_base=diff_base,
                 dirty=bool(git(repo, "status", "--porcelain", "-z")), pr=metadata,
                 files=files, hunks=hunks)
    inv["id"] = digest(inv)
    save(output / "inventory.json", inv)
    return inv


def read(bundle):
    inv = load(Path(bundle) / "inventory.json", "inventory")
    if digest({k: v for k, v in inv.items() if k != "id"}) != inv["id"]:
        raise ValueError("inventory identity mismatch")
    for h in inv["hunks"]:
        data = local_ref(bundle, h["ref"]).read_bytes()
        if len(data) != h["bytes"] or digest(data) != h["sha256"]:
            raise ValueError("changed or missing pinned hunk: " + h["id"])
    return inv


def summary(inv):
    return {"id": inv["id"], "base": inv["base"], "head": inv["head"], "diff_base": inv["diff_base"],
            "dirty": inv["dirty"], "files": inv["files"], "hunks": inv["hunks"]}


def support(inv, spec):
    if spec["revision"] not in {inv["base"], inv["head"], inv["diff_base"]}:
        raise ValueError("support must use pinned revisions")
    if spec["start"] < 1 or spec["end"] < spec["start"]:
        raise ValueError("invalid evidence line range")
    data = git(inv["repo"], "show", spec["revision"] + ":" + spec["path"])
    lines = data.splitlines(keepends=True)
    if spec["end"] > len(lines):
        raise ValueError("evidence range exceeds file")
    return b"".join(lines[spec["start"] - 1:spec["end"]])
