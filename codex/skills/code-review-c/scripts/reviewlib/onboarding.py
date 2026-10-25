"""Offline campaign preparation, isolated trials, human grading, and promotion.

No function except run() launches a model. Campaigns and answer keys are private.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import inventory, planner
from .records import SKILL, digest, external, instruction_hash, load, local_ref, record, save, validate_output

AXES = {"patch_size": ["below", "around", "above"], "support": ["low", "high"],
        "file_count": ["few", "many"], "languages": ["single", "related", "unrelated"],
        "defect_position": ["early", "middle", "late", "clean"]}


def measure_dimensions(inv, bundle, fixture):
    patch_bytes = sum(len(local_ref(bundle, h["ref"]).read_bytes().decode("utf-8", "backslashreplace").encode()) for h in inv["hunks"])
    support_bytes = sum(len(inventory.support(inv, e).decode("utf-8", "backslashreplace").encode()) for e in fixture.get("evidence", []))
    langs = {h["language"] for h in inv["hunks"]}
    count = len(inv["files"])
    measured = {"patch_size": "below" if patch_bytes < 56000 else "around" if patch_bytes <= 60000 else "above",
                "support": "low" if support_bytes < 8000 else "high",
                "file_count": "few" if count <= 4 else "many" if count >= 10 else "intermediate"}
    if any(fixture["dimensions"][k] != v for k, v in measured.items()):
        raise ValueError("declared fixture dimensions disagree with measured patch/support/files")
    relation = fixture["dimensions"]["languages"]
    if (relation == "single") != (len(langs) == 1):
        raise ValueError("language relationship disagrees with fixture languages")
    if relation == "related" and (not fixture.get("relationship") or not fixture.get("evidence")):
        raise ValueError("related languages require an interface explanation and pinned evidence")


def prepare(output):
    output = external(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("prepare needs a new or empty private directory")
    save(output / "matrix.json", record("fixture-matrix", axes=AXES,
        strategy_controls=["focused", "combined"], repetitions=3, splits=["train", "heldout"],
        notes="Author real pinned fixtures and gold keys; no model calls are launched."))
    spec = record("campaign-spec", identity={"model": "", "reasoning_effort": "", "codex_version": "",
                  "instruction_hash": instruction_hash()}, scope={"languages": ["python"], "domain": "backend", "aspect": "correctness"},
                  baseline="focused", strategies=[{"id": "focused", "layout": "focused", "aspects": ["correctness"],
                    "limits": {"total_visible_bytes": 58000, "patch_bytes": 58000, "support_bytes": 58000, "file_count": 100}}],
                  fixtures=[], limits={"max_calls": 0, "max_wall_seconds": 0, "trial_seconds": 0})
    save(output / "spec.json", spec)
    return spec


def archive(repo, rev, destination):
    """Only regular files/directories; fixture links never expose host answer keys."""
    entries = inventory.git(repo, "ls-tree", "-r", "-z", rev).split(b"\0")
    files = []
    for entry in filter(None, entries):
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("fixtures must not contain symlinks, submodules, or special files")
        files.append((mode, oid, name))
    # Batch raw blobs so export attributes cannot omit or rewrite pinned files.
    raw = subprocess.check_output(["git", "--no-pager", "-C", str(repo), "cat-file", "--batch"],
                                  input=b"".join(oid + b"\n" for _, oid, _ in files), stderr=subprocess.PIPE)
    blobs = io.BytesIO(raw)
    destination.mkdir(parents=True)
    for mode, oid, name in files:
        header = blobs.readline().split()
        if len(header) != 3 or header[:2] != [oid, b"blob"]:
            raise ValueError("invalid pinned fixture blob")
        size = int(header[2])
        data = blobs.read(size)
        if len(data) != size or blobs.read(1) != b"\n":
            raise ValueError("incomplete pinned fixture blob")
        path = local_ref(destination, os.fsdecode(name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if mode == b"100755" else 0o644)


def tree_hash(root):
    root = Path(root)
    return digest({p.relative_to(root).as_posix(): {"sha256": digest(p.read_bytes()),
                                                  "executable": p.stat().st_mode & 0o111}
                   for p in sorted(root.rglob("*")) if p.is_file()})


def freeze(spec_path, output):
    spec = load(spec_path, "campaign-spec")
    output = external(output, *[f["repo"] for f in spec["fixtures"]])
    if output.exists() and any(output.iterdir()):
        raise ValueError("freeze needs a new or empty private directory")
    identity = spec["identity"]
    if any(not identity.get(k) for k in ("model", "reasoning_effort", "codex_version", "instruction_hash")):
        raise ValueError("freeze requires exact resolved reviewer identity")
    if identity["instruction_hash"] != instruction_hash():
        raise ValueError("freeze requires current skill hash")
    if not spec["fixtures"] or not spec["strategies"]:
        raise ValueError("author fixtures and strategies before freezing")
    if spec["baseline"] not in {s["id"] for s in spec["strategies"]}:
        raise ValueError("unknown focused baseline")
    if spec["scope"]["aspect"] not in (*planner.ESSENTIAL, *planner.FOCUSED, *planner.OPTIONAL):
        raise ValueError("unknown campaign aspect")
    if spec["scope"]["domain"] not in {"backend", "generic"}:
        raise ValueError("unknown campaign domain")
    if not spec["scope"]["languages"] or len(set(spec["scope"]["languages"])) != len(spec["scope"]["languages"]):
        raise ValueError("profile scope needs distinct tested languages")
    spec["scope"]["languages"] = sorted(spec["scope"]["languages"])
    seen = set()
    for s in spec["strategies"]:
        _identifier(s["id"])
        if s["id"] in seen or s["layout"] not in {"focused", "combined"}:
            raise ValueError("duplicate strategy or unknown layout")
        seen.add(s["id"])
        if not s["aspects"] or s["aspects"][0] != spec["scope"]["aspect"] or len(set(s["aspects"])) != len(s["aspects"]):
            raise ValueError("strategy must start with the campaign's primary aspect")
        if s["layout"] == "focused" and len(s["aspects"]) != 1:
            raise ValueError("focused strategy needs one purpose")
        if s["layout"] == "combined" and len(s["aspects"]) < 2:
            raise ValueError("combined control needs multiple aspects")
        if any(type(s["limits"].get(k)) is not int or s["limits"][k] <= 0 for k in ("total_visible_bytes", "patch_bytes", "support_bytes", "file_count")):
            raise ValueError("invalid strategy limits")
    baseline = next(s for s in spec["strategies"] if s["id"] == spec["baseline"])
    if baseline["layout"] != "focused":
        raise ValueError("baseline must be focused")
    fixtures, keys, seen = [], {}, set()
    for f in spec["fixtures"]:
        host_input_bytes = f.get("host_input_bytes", 8000)
        if type(host_input_bytes) is not int or host_input_bytes < 0:
            raise ValueError("host visible-input reservation must be a nonnegative integer")
        _identifier(f["id"])
        if f["id"] in seen or f["split"] not in {"train", "heldout"}:
            raise ValueError("duplicate fixture or invalid split")
        seen.add(f["id"])
        for axis, values in AXES.items():
            if f["dimensions"].get(axis) not in values:
                raise ValueError("fixture missing dimension: " + axis)
        gold = f["gold"]
        if len({g["id"] for g in gold}) != len(gold) or any(type(g["critical"]) is not bool or not g["criterion"] for g in gold):
            raise ValueError("invalid gold key")
        if (not gold) != (f["dimensions"]["defect_position"] == "clean"):
            raise ValueError("clean controls must have no gold defects")
        root = output / "fixtures" / f["id"]
        inv = inventory.build(f["repo"], root / "bundle", f["base"], f["head"])
        if not inv["hunks"] or any(h["binary"] or h["opaque"] for h in inv["hunks"]):
            raise ValueError("onboarding fixtures need nonempty text patches")
        measure_dimensions(inv, root / "bundle", f)
        archive(f["repo"], inv["diff_base"], root / "workspace" / "base")
        archive(f["repo"], inv["head"], root / "workspace" / "head")
        # No keys, campaign manifest, or original paths are mounted into the trial container.
        prompts = {}
        for strategy in spec["strategies"]:
            langs = sorted({h["language"] for h in inv["hunks"]})
            if not set(langs) <= set(spec["scope"]["languages"]):
                raise ValueError("fixture language falls outside measured profile scope")
            task = planner._task(0, spec["scope"]["aspect"], langs, spec["scope"]["domain"],
                "calibration fixture", {"id": "candidate", "limits": strategy["limits"]},
                host_input_bytes, [])
            task["hunks"] = [h["id"] for h in inv["hunks"]]
            task["evidence"] = [{**e, "sha256": digest(inventory.support(inv, e))} for e in f.get("evidence", [])]
            for aspect in strategy["aspects"][1:]:
                for ref in planner.instructions(aspect, langs, spec["scope"]["domain"]):
                    if ref not in task["instructions"]:
                        task["instructions"].append(ref)
            tail = ("\nCalibration: return only a calibration-result JSON matching the supplied schema. "
                       "Read pinned trees in /workspace/base and /workspace/head as needed. "
                       "No agents, network browsing, or files outside these trees. "
                       "Report extra source bytes read in extra_visible_bytes; mark incomplete if total input exceeds the assigned limit.\n")
            prompt, accounting = planner.render(task, inv, root / "bundle", tail)
            prompt_path = root / (strategy["id"] + ".txt")
            prompt_path.write_text(prompt)
            prompts[strategy["id"]] = {"ref": prompt_path.relative_to(output).as_posix(),
                "sha256": digest(prompt.encode()), "bytes": accounting,
                "fits": planner.fits(task, accounting, {h["id"]: h for h in inv["hunks"]}),
                "hunks": task["hunks"]}
        keys[f["id"]] = gold
        fixtures.append({"id": f["id"], "split": f["split"], "dimensions": f["dimensions"],
                         "file_count": len(inv["files"]), "languages": sorted({h["language"] for h in inv["hunks"]}),
                         "workspace_hash": tree_hash(root / "workspace"), "prompts": prompts})
    if set(spec["scope"]["languages"]) != {language for f in fixtures for language in f["languages"]}:
        raise ValueError("each scope language needs measured fixture evidence")
    campaign = record("campaign", identity=identity, scope=spec["scope"], baseline=spec["baseline"],
                      strategies=spec["strategies"], limits=spec["limits"], fixtures=fixtures,
                      keys_hash=digest(keys), repetitions=3)
    campaign["id"] = digest(campaign)
    save(output / "grading" / "keys.json", keys)
    save(output / "campaign.json", campaign)
    return campaign


def _identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value) or "--" in value:
        raise ValueError("unsafe record identifier")


def read(root):
    c = load(Path(root) / "campaign.json", "campaign")
    if digest({k: v for k, v in c.items() if k != "id"}) != c["id"]:
        raise ValueError("frozen campaign changed")
    if c["identity"]["instruction_hash"] != instruction_hash():
        raise ValueError("skill changed since campaign freeze")
    if digest(load(Path(root) / "grading" / "keys.json")) != c["keys_hash"]:
        raise ValueError("answer keys changed after freezing")
    for f in c["fixtures"]:
        if tree_hash(Path(root) / "fixtures" / f["id"] / "workspace") != f["workspace_hash"]:
            raise ValueError("fixture trees changed")
        for p in f["prompts"].values():
            if digest(local_ref(root, p["ref"]).read_bytes()) != p["sha256"]:
                raise ValueError("fixture prompt changed")
    return c


def run(root, image, host_config, auth, max_calls, max_wall_seconds, trial_seconds):
    """Docker is required to keep private keys physically outside reviewer mounts.

    The caller supplies an image with Codex installed, pinned by immutable digest.
    Config contains only host-selected model/effort. No model choice is invented.
    """
    root = external(root)
    c = read(root)
    if not re.fullmatch(r"[\w./:-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("trial image must be pinned by sha256 digest")
    limits = c["limits"]
    for key, value in (("max_calls", max_calls), ("max_wall_seconds", max_wall_seconds), ("trial_seconds", trial_seconds)):
        if type(value) is not int or value <= 0 or value > limits[key]:
            raise ValueError("explicit positive run limits must fit the frozen campaign")
    # A previously started trial still spends its call allowance after a crash.
    attempts = [load(p) for p in (root / "attempts").glob("*/receipt.json")]
    used_calls = sum(bool(r.get("launched")) for r in attempts)
    used_time = sum(max(r.get("reserved_seconds", trial_seconds), r.get("elapsed_seconds", trial_seconds))
                    if r.get("status") in {"started", "interrupted", "cleanup-failed"}
                    else r.get("elapsed_seconds", trial_seconds) for r in attempts)
    config = __import__("tomllib").loads(Path(host_config).read_text())
    if set(config) != {"model", "model_reasoning_effort"}:
        raise ValueError("provide a sanitized host config with exactly model and model_reasoning_effort")
    if (config["model"], config["model_reasoning_effort"]) != (c["identity"]["model"], c["identity"]["reasoning_effort"]):
        raise ValueError("host-selected identity differs from frozen campaign")
    if not shutil.which("docker"):
        raise ValueError("Docker is required for isolated model trials")
    probe = "crc-version-" + uuid.uuid4().hex
    print("Version probe container: " + probe, file=sys.stderr, flush=True)
    try:
        version = subprocess.check_output(["docker", "run", "--rm", "--name", probe, "--network=none",
                                           "--entrypoint", "codex", image, "--version"], text=True, timeout=30).strip()
    finally:
        try:
            cleanup = subprocess.run(["docker", "rm", "-f", probe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if cleanup.returncode:
                raise ValueError("version probe cleanup failed; inspect and remove " + probe)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("version probe cleanup failed; inspect and remove " + probe) from error
    if version != c["identity"]["codex_version"]:
        raise ValueError("container Codex version differs from frozen identity")
    schema = SKILL / "references" / "schemas" / "calibration-result.json"
    start, launched = time.monotonic(), 0
    for fixture in c["fixtures"]:
        for strategy in c["strategies"]:
            prompt = fixture["prompts"][strategy["id"]]
            for repeat in range(3):
                key = f'{fixture["id"]}--{strategy["id"]}--{repeat}'
                trial = root / "attempts" / key
                if trial.exists():
                    continue  # Never overwrite interrupted, failed, or ungraded attempts.
                elapsed = time.monotonic() - start
                if (launched >= max_calls or used_calls + launched >= limits["max_calls"]
                        or elapsed >= max_wall_seconds or used_time + elapsed >= limits["max_wall_seconds"]):
                    return {"launched": launched, "reason": "explicit campaign limit reached"}
                trial.mkdir(parents=True)
                receipt = record("trial-receipt", campaign_id=c["id"], fixture=fixture["id"],
                    strategy=strategy["id"], repeat=repeat, identity=c["identity"], input_hash=prompt["sha256"],
                    launched=False, status="over-budget" if not prompt["fits"] else "started",
                    elapsed_seconds=0, tokens=None, result_hash=None, trace_hash=None)
                save(trial / "receipt.json", receipt)
                if not prompt["fits"]:
                    continue
                timeout = min(trial_seconds, max_wall_seconds - elapsed, limits["max_wall_seconds"] - used_time - elapsed)
                with tempfile.TemporaryDirectory(prefix="crc-trial-") as temp:
                    temp = Path(temp)
                    for name in ("home", "input", "output"):
                        (temp / name).mkdir()
                    (temp / "home" / "config.toml").write_text(Path(host_config).read_text())
                    (temp / "home" / "auth.json").write_bytes(Path(auth).read_bytes())
                    (temp / "home" / "auth.json").chmod(0o600)
                    shutil.copyfile(local_ref(root, prompt["ref"]), temp / "input" / "prompt.txt")
                    shutil.copyfile(schema, temp / "input" / "schema.json")
                    container = "crc-" + digest(str(trial))[:20]
                    command = ["docker", "run", "--rm", "--interactive", "--name", container, "--entrypoint", "codex",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--user", f"{os.getuid()}:{os.getgid()}",
                        "-e", "CODEX_HOME=/codex-home", "-e", "HOME=/tmp",
                        "--mount", f'type=bind,src={temp / "home"},dst=/codex-home',
                        "--mount", f'type=bind,src={temp / "input"},dst=/input,readonly',
                        "--mount", f'type=bind,src={temp / "output"},dst=/output',
                        "--mount", f'type=bind,src={root / "fixtures" / fixture["id"] / "workspace"},dst=/workspace,readonly',
                        image, "exec", "--ephemeral", "--json", "--sandbox", "read-only",
                        "--skip-git-repo-check", "-C", "/workspace", "--output-schema", "/input/schema.json",
                        "-o", "/output/result.json", "-"]
                    receipt["launched"] = True
                    receipt["reserved_seconds"] = timeout
                    save(trial / "receipt.json", receipt)
                    launched += 1
                    began = time.monotonic()
                    try:
                        with (trial / "trace.jsonl").open("wb") as stdout, (trial / "stderr.txt").open("wb") as stderr:
                            result = subprocess.run(command, input=(temp / "input" / "prompt.txt").read_bytes(),
                                                    stdout=stdout, stderr=stderr, timeout=timeout)
                        receipt["status"] = "finished" if result.returncode == 0 else "failed"
                    except KeyboardInterrupt:
                        receipt["status"] = "interrupted"
                        raise
                    except subprocess.TimeoutExpired:
                        receipt["status"] = "timeout"
                    except OSError as error:
                        receipt["status"] = "failed"
                        receipt["error"] = str(error)
                    finally:
                        try:
                            cleanup = subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                            if cleanup.returncode:
                                receipt["status"] = "cleanup-failed"
                        except (OSError, subprocess.TimeoutExpired):
                            receipt["status"] = "cleanup-failed"
                        receipt["elapsed_seconds"] = time.monotonic() - began
                        if (temp / "output" / "result.json").is_file() and not (temp / "output" / "result.json").is_symlink():
                            shutil.copyfile(temp / "output" / "result.json", trial / "result.json")
                            receipt["result_hash"] = digest((trial / "result.json").read_bytes())
                        receipt["trace_hash"] = digest((trial / "trace.jsonl").read_bytes()) if (trial / "trace.jsonl").exists() else None
                        receipt["tokens"] = _tokens(trial / "trace.jsonl")
                        save(trial / "receipt.json", receipt)
                    if receipt["status"] == "cleanup-failed":
                        return {"launched": launched, "reason": "container cleanup failed; stop and inspect retained receipt"}
    return {"launched": launched, "reason": "all planned attempts retained"}


def _tokens(path):
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if any(e.get("type") in {"error", "turn.failed"} for e in events):
            return None
        usages = [e["usage"] for e in events if e.get("type") == "turn.completed"]
        if not usages:
            return None
        return sum(u["input_tokens"] + u["output_tokens"] for u in usages)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def grade(root, attempt, decisions_path):
    c = read(root)
    _identifier_attempt(attempt)
    trial = Path(root) / "attempts" / attempt
    receipt = load(trial / "receipt.json", "trial-receipt")
    if receipt["campaign_id"] != c["id"]:
        raise ValueError("trial belongs to another campaign")
    decisions = load(decisions_path, "grading-decisions")
    result = load(trial / "result.json", "calibration-result") if (trial / "result.json").exists() else None
    gold = load(Path(root) / "grading" / "keys.json")[receipt["fixture"]]
    grade_record = calculate_grade(c, receipt, result, gold, decisions["decisions"])
    save(trial / "grade.json", grade_record)
    return grade_record


def calculate_grade(c, receipt, result, gold, entries):
    if result is not None:
        validate_output(result, "calibration-result")
        fixture = next(f for f in c["fixtures"] if f["id"] == receipt["fixture"])
        owned = set(fixture["prompts"][receipt["strategy"]]["hunks"])
        if set(result["inspected_hunks"]) - owned or len(set(result["inspected_hunks"])) != len(result["inspected_hunks"]):
            raise ValueError("invalid calibration inspected hunks")
        for finding in result["findings"]:
            if not finding["hunks"] or not set(finding["hunks"]) <= owned or not finding["evidence"] or not finding["claim"]:
                raise ValueError("calibration finding needs owned hunks, claim and evidence")
    findings = result["findings"] if result else []
    ids = [f["id"] for f in findings]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate calibration finding")
    if len(entries) != len(ids) or {d["finding"] for d in entries} != set(ids):
        raise ValueError("grade every finding, preserving false positives and unresolved findings")
    matched, unresolved, fp = set(), [], []
    for d in entries:
        if not d["evidence"] or d["disposition"] not in {"matched", "false-positive", "unresolved"}:
            raise ValueError("each grade needs evidence and a disposition")
        if d["disposition"] == "matched":
            if d["gold"] not in {g["id"] for g in gold} or d["gold"] in matched:
                raise ValueError("gold matches must be valid and one-to-one")
            matched.add(d["gold"])
        elif d["disposition"] == "unresolved":
            unresolved.append(d["finding"])
        else:
            fp.append(d["finding"])
    return record("trial-grade", campaign_id=c["id"], receipt_hash=digest(receipt),
                         decisions=entries, matched=sorted(matched), false_positives=fp, unresolved=unresolved,
                         critical_misses=[g["id"] for g in gold if g["critical"] and g["id"] not in matched],
                         recall=len(matched) / len(gold) if gold else 1.0,
                         precision=len(matched) / len(findings) if findings else 1.0)


def _identifier_attempt(value):
    if not re.fullmatch(r"[\w-]+--[\w-]+--[012]", value):
        raise ValueError("unsafe attempt ID")


def compare(root):
    c = read(root)
    keys = load(Path(root) / "grading" / "keys.json")
    missing, scores = [], {}
    for strategy in c["strategies"]:
        trials = []
        for fixture in c["fixtures"]:
            prompt = fixture["prompts"][strategy["id"]]
            for repeat in range(3):
                key = f'{fixture["id"]}--{strategy["id"]}--{repeat}'
                trial = Path(root) / "attempts" / key
                try:
                    receipt = load(trial / "receipt.json", "trial-receipt")
                    g = load(trial / "grade.json", "trial-grade")
                    result = load(trial / "result.json", "calibration-result")
                    if g != calculate_grade(c, receipt, result, keys[fixture["id"]], g["decisions"]):
                        raise ValueError("grade does not match retained decisions/output/gold")
                    trace = trial / "trace.jsonl"
                    if (receipt["campaign_id"] != c["id"] or receipt["identity"] != c["identity"]
                            or receipt["input_hash"] != prompt["sha256"] or g["receipt_hash"] != digest(receipt)
                            or receipt["result_hash"] != digest((trial / "result.json").read_bytes())
                            or receipt["trace_hash"] != digest(trace.read_bytes())
                            or receipt["tokens"] != _tokens(trace) or receipt["tokens"] is None
                            or receipt["fixture"] != fixture["id"] or receipt["strategy"] != strategy["id"] or receipt["repeat"] != repeat):
                        raise ValueError("incompatible or incomplete trial receipt")
                    complete = (receipt["launched"] and receipt["status"] == "finished"
                                and result["status"] == "complete" and not result["gaps"]
                                and set(result["inspected_hunks"]) == set(prompt["hunks"])
                                and type(result["extra_visible_bytes"]) is int and result["extra_visible_bytes"] >= 0
                                and result["extra_visible_bytes"] + prompt["bytes"]["total"] <= strategy["limits"]["total_visible_bytes"]
                                and not g["unresolved"]
                                and receipt["elapsed_seconds"] > 0)
                    if not complete:
                        missing.append(key)
                    trials.append({"key": key, "split": fixture["split"], "complete": complete and not g["critical_misses"],
                                   "recall": g["recall"], "precision": g["precision"],
                                   "measured": {"total_visible_bytes": prompt["bytes"]["total"] + result["extra_visible_bytes"],
                                                "patch_bytes": prompt["bytes"]["patch"],
                                                "support_bytes": prompt["bytes"]["evidence"] + result["extra_visible_bytes"],
                                                "file_count": fixture["file_count"]},
                                   "tokens": receipt["tokens"], "latency_seconds": receipt["elapsed_seconds"],
                                   "receipt_hash": digest(receipt), "grade_hash": digest(g)})
                except (OSError, ValueError, KeyError, TypeError):
                    missing.append(key)
        scores[strategy["id"]] = {"id": strategy["id"], "layout": strategy["layout"], "limits": strategy["limits"],
            "trials": trials, "complete": len(trials) == len(c["fixtures"]) * 3 and all(t["complete"] for t in trials)}
    baseline = scores[c["baseline"]]
    axes = {**AXES, "languages": AXES["languages"] if len(c["scope"]["languages"]) > 1 else ["single"]}
    dimensions_ok = all(set(v) <= {f["dimensions"][k] for f in c["fixtures"]} for k, v in axes.items())
    splits_ok = {f["split"] for f in c["fixtures"]} == {"train", "heldout"}
    # Trees cannot occur in both training and held-out sets, regardless of fixture ID.
    train_hashes = {f["workspace_hash"] for f in c["fixtures"] if f["split"] == "train"}
    splits_ok = splits_ok and not train_hashes & {f["workspace_hash"] for f in c["fixtures"] if f["split"] == "heldout"}
    splits_ok = splits_ok and all(any(keys[f["id"]] for f in c["fixtures"] if f["split"] == split)
        and any(not keys[f["id"]] for f in c["fixtures"] if f["split"] == split) for split in ("train", "heldout"))
    layouts_ok = {s["layout"] for s in c["strategies"]} == {"focused", "combined"}
    for s in scores.values():
        s["measured_limits"] = {k: min(s["limits"][k], max((t["measured"][k] for t in s["trials"]), default=0))
                                for k in s["limits"]}
        for split in ("train", "heldout"):
            subset = [t for t in s["trials"] if t["split"] == split]
            s[split] = {key: sum(t[key] for t in subset) / len(subset) if subset else None
                        for key in ("recall", "precision", "tokens", "latency_seconds")}
    for s in scores.values():
        s["passing"] = bool(s["complete"] and baseline["complete"] and dimensions_ok and splits_ok and layouts_ok
            and all(s[split][m] >= baseline[split][m] for split in ("train", "heldout") for m in ("recall", "precision")))
    ordered = sorted(scores.values(), key=lambda s: (not s["passing"],
        -(s["heldout"]["recall"] or 0), -(s["heldout"]["precision"] or 0),
        -(s["train"]["recall"] or 0), -(s["train"]["precision"] or 0),
        s["heldout"]["tokens"] if s["heldout"]["tokens"] is not None else float("inf"),
        s["heldout"]["latency_seconds"] if s["heldout"]["latency_seconds"] is not None else float("inf"), s["id"]))
    report = record("comparison", campaign_id=c["id"], identity=c["identity"], scope=c["scope"],
                    qualifying=not missing and dimensions_ok and splits_ok and layouts_ok,
                    missing=missing, matrix_complete=dimensions_ok, heldout_separate=splits_ok,
                    strategies=ordered)
    save(Path(root) / "comparison.json", report)
    return report


def promote(root, strategy, output):
    c = read(root)
    repositories = [inventory.read(Path(root) / "fixtures" / f["id"] / "bundle")["repo"] for f in c["fixtures"]]
    output = external(output, *repositories)
    report = compare(root)
    selected = next(s for s in report["strategies"] if s["id"] == strategy)
    if not report["qualifying"] or not selected["passing"]:
        raise ValueError("incomplete campaign, critical miss, failure, or quality regression blocks promotion")
    # Combined controls measure interference; the production skill always uses one primary purpose.
    if selected["layout"] != "focused":
        raise ValueError("combined controls cannot calibrate this release's focused instruction layout")
    focused = [s for s in report["strategies"] if s["passing"] and s["layout"] == "focused"]
    if focused[0]["id"] != strategy:
        raise ValueError("promote the highest-quality passing focused strategy")
    report_ref = "evidence/" + digest(report) + ".json"
    profile = record("model-profile", id=digest({"campaign": c["id"], "strategy": strategy}),
        status="promoted", identity=c["identity"], scope=c["scope"], strategy=strategy,
        limits=selected["measured_limits"], quality={k: selected["heldout"][k] for k in ("recall", "precision")},
        cost={"tokens": selected["heldout"]["tokens"], "latency_seconds": selected["heldout"]["latency_seconds"]},
        evidence={"report": report_ref, "sha256": digest(report), "campaign_id": c["id"]})
    save(output / report_ref, report)
    save(output / (profile["id"] + ".json"), profile)
    return profile


def import_evidence(root, source, format_name):
    c = read(root)
    source = Path(source)
    if format_name == "native":
        old = read(source)
        if old["id"] == c["id"]:
            imported = 0
            for attempt in sorted((source / "attempts").iterdir()):
                target = Path(root) / "attempts" / attempt.name
                if target.exists():
                    raise ValueError("import would overwrite retained attempt")
                shutil.copytree(attempt, target)
                imported += 1
            return {"imported": imported, "compatibility": "exact frozen campaign; compare revalidates receipts"}
    # Optional skill-bench adapter: accept an exported JSON evidence manifest, never execute it.
    data = load(source) if source.is_file() else load(source / "campaign.json")
    ref = Path(root) / "historical" / (digest(data) + ".json")
    save(ref, record("historical-evidence", format=format_name, source=str(source.resolve()),
                     data=data, compatible=False,
                     reason="Historical comparison/fixture leads only; not capacity evidence for this skill."))
    return {"imported": 1, "compatibility": "historical only"}
