import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "codex/skills/code-review-c"
sys.path.insert(0, str(SKILL / "scripts"))
from reviewlib import inventory, planner
from reviewlib.records import digest, instruction_hash, record, save


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "--allow-empty", "-m", "Fixture revision")
    return git(repo, "rev-parse", "HEAD")


def init(repo):
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.autocrlf", "false")


def identity():
    return {"model": "synthetic-exact-model", "reasoning_effort": "high",
            "codex_version": "codex-cli synthetic", "instruction_hash": instruction_hash()}


def rehash(plan):
    plan["id"] = digest({k: v for k, v in plan.items() if k != "id"})
    return plan


def receipts(plan):
    return [record("task-result", plan_id=plan["id"], task_id=t["id"], input_hash=t["input_hash"],
                   identity=plan["identity"], status="complete", inspected_hunks=t["hunks"],
                   extra_visible_bytes=0, findings=[], gaps=[]) for t in plan["tasks"]]


def candidate(task, category="defect", priority=1):
    return {"id": "f1", "category": category, "priority": priority, "hunks": task["hunks"],
            "claim": "Changed branch accepts the wrong owner", "evidence": ["Pinned changed branch and base counterexample"]}


class GitCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crc-test-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        init(self.repo)
        (self.repo / "a.py").write_text("def value():\n    return 1\n")
        self.base = commit(self.repo)
        (self.repo / "a.py").write_text("def value():\n    return 2\n")
        self.head = commit(self.repo)
        self.bundle = self.root / "bundle"

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self):
        return inventory.build(self.repo, self.bundle, self.base, self.head)

    def plan(self, analysis=None, **kwargs):
        self.inv = self.inventory()
        return planner.build(self.bundle, analysis, **kwargs)

    def fixture(self, name, size=1000, files=1, high=False, split="train", position="early"):
        repo = self.root / name
        init(repo)
        (repo / "context.py").write_text("CONTEXT = '" + "s" * (10000 if high else 100) + "'\n")
        for i in range(files):
            (repo / f"f{i}.py").write_text(f"def f{i}():\n    return 1\n")
        base = commit(repo)
        for i in range(files):
            (repo / f"f{i}.py").write_text(f"def f{i}():\n    return 2\n" + ("# " + name + " " + "x" * size + "\n" if i == 0 else ""))
        head = commit(repo)
        return {"id": name, "repo": str(repo), "base": base, "head": head, "split": split,
                "dimensions": {"patch_size": "below" if size < 56000 else "around" if size <= 60000 else "above",
                               "support": "high" if high else "low", "file_count": "many" if files >= 10 else "few",
                               "languages": "single", "defect_position": position},
                "gold": [] if position == "clean" else [{"id": "g1", "critical": True, "criterion": "Synthetic gold obligation"}],
                "evidence": [{"revision": head, "path": "context.py", "start": 1, "end": 1}]}

    def campaign_spec(self, full=False):
        fixtures = []
        for split in (("train", "heldout") if full else ("train",)):
            for i, pos in enumerate(("early", "middle", "late", "clean") if full else ("early",)):
                fixtures.append(self.fixture(f"{split}{i}", [1000, 57000, 65000, 1000][i],
                                             files=10 if i == 2 else 1, high=i == 1, split=split, position=pos))
        limits = {"total_visible_bytes": 200000, "patch_bytes": 150000, "support_bytes": 100000, "file_count": 50}
        spec = record("campaign-spec", identity=identity(), scope={"languages": ["python"], "domain": "backend", "aspect": "correctness"},
                      baseline="focused", strategies=[{"id": "focused", "layout": "focused", "aspects": ["correctness"], "limits": limits},
                        {"id": "combined", "layout": "combined", "aspects": ["correctness", "security"], "limits": copy.deepcopy(limits)}],
                      fixtures=fixtures, limits={"max_calls": 200, "max_wall_seconds": 10000, "trial_seconds": 120})
        save(self.root / "spec.json", spec)
        return spec


def synthetic_trials(root, c):
    """Create explicitly synthetic receipts; no model or Docker invocation."""
    from reviewlib import onboarding
    gold = json.loads((root / "grading/keys.json").read_text())
    for fixture in c["fixtures"]:
        for strategy in c["strategies"]:
            prompt = fixture["prompts"][strategy["id"]]
            for repeat in range(3):
                name = f'{fixture["id"]}--{strategy["id"]}--{repeat}'
                trial = root / "attempts" / name
                trial.mkdir(parents=True)
                finding = {"id": "f1", "priority": 1, "category": "defect", "claim": "Synthetic finding",
                           "hunks": prompt["hunks"], "evidence": ["Synthetic pinned evidence"]}
                result = record("calibration-result", status="complete", inspected_hunks=prompt["hunks"], extra_visible_bytes=0,
                                findings=[finding] if gold[fixture["id"]] else [], gaps=[])
                save(trial / "result.json", result)
                trace = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}) + "\n"
                (trial / "trace.jsonl").write_text(trace)
                receipt = record("trial-receipt", campaign_id=c["id"], fixture=fixture["id"], strategy=strategy["id"], repeat=repeat,
                                 identity=c["identity"], input_hash=prompt["sha256"], launched=True, status="finished", elapsed_seconds=1,
                                 tokens=120, result_hash=digest((trial / "result.json").read_bytes()), trace_hash=digest(trace.encode()))
                save(trial / "receipt.json", receipt)
                decisions = record("grading-decisions", decisions=[{"finding": "f1", "disposition": "matched", "gold": "g1", "evidence": ["Synthetic gold match"]}] if result["findings"] else [])
                save(trial / "decisions.json", decisions)
                onboarding.grade(root, name, trial / "decisions.json")
