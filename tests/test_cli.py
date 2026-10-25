"""Exercise the public CLI and its on-disk handoffs without model calls."""
import hashlib
import json
import subprocess
import sys

from support import GitCase, SKILL, commit


class CliTests(GitCase):
    def cli(self, *args, expected=0):
        completed = subprocess.run(
            [sys.executable, str(SKILL / "scripts/review.py"), *map(str, args)],
            cwd=self.root, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, expected, completed.stderr)
        if expected:
            self.assertEqual(completed.stdout, "")
            return json.loads(completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def prepare(self):
        inventory = self.cli("inventory", "--repo", self.repo, "--base", self.base,
                             "--head", self.head, "--out", self.bundle)
        self.assertEqual((inventory["base"], inventory["head"]), (self.base, self.head))
        self.assertEqual([f["path"] for f in inventory["files"]], ["a.py"])
        analysis = self.write_json(self.root / "analysis.json", {
            "files": {"a.py": {"aspects": ["simplification"]}},
            "optional_reason": "Inspect a possible simplification",
        })
        self.plan_path = self.root / "plan.json"
        self.plan_record = self.cli("plan", "--bundle", self.bundle, "--out", self.plan_path,
                                    "--analysis", analysis)
        self.assertEqual(json.loads(self.plan_path.read_text()), self.plan_record)
        self.assertTrue(self.cli("validate", "--bundle", self.bundle,
                                 "--plan", self.plan_path)["valid"])
        self.results_dir = self.root / "results"
        self.results_dir.mkdir()
        return self.plan_record

    def outcome(self, command, verification=None, **options):
        args = [command, "--bundle", self.bundle, "--plan", self.plan_path,
                "--results", self.results_dir]
        if command == "next":
            args += ["--capacity", 20]
        if verification:
            args += ["--verification", verification]
        return self.cli(*args, **options)

    def receipt(self, task, findings=()):
        # Render each launched task through the CLI and bind receipts to its bytes.
        prompt_path = self.root / "prompts" / (task["id"] + ".txt")
        rendered = self.cli("prompt", "--bundle", self.bundle, "--plan", self.plan_path,
                            "--task", task["id"], "--out", prompt_path)
        self.assertEqual(rendered["input_hash"], hashlib.sha256(prompt_path.read_bytes()).hexdigest())
        self.assertEqual(rendered["bytes"], task["bytes"])
        self.assertIn("return 2", prompt_path.read_text())
        return self.write_json(self.results_dir / (task["id"] + ".json"), {
            "kind": "task-result", "version": 1, "plan_id": self.plan_record["id"],
            "task_id": task["id"], "input_hash": rendered["input_hash"],
            "identity": self.plan_record["identity"], "status": "complete",
            "inspected_hunks": task["hunks"], "extra_visible_bytes": 0,
            "findings": list(findings), "gaps": [],
        })

    def finding(self, task, category="defect"):
        return {"id": "f1", "category": category, "priority": 2, "hunks": task["hunks"],
                "claim": "Synthetic fixture finding", "evidence": ["Pinned a.py changed return value"]}

    def verification(self, task, disposition="verified"):
        return self.write_json(self.root / "verification.json", {
            "kind": "verification", "version": 1, "plan_id": self.plan_record["id"],
            "decisions": [{"candidate": task["id"] + ":f1", "disposition": disposition,
                           "evidence": ["Synthetic parent compared pinned base and head"]}],
        })

    def test_verified_defect_is_reported_and_optional_work_is_skipped(self):
        plan = self.prepare()
        initial = self.outcome("next")
        self.assertEqual(initial["gate"], "pending")
        self.assertEqual(set(initial["launch"]), {t["id"] for t in plan["tasks"] if t["stage"] == "essential"})
        pending = self.outcome("report")
        self.assertEqual(pending["gate"], "pending")
        self.assertTrue(pending["coverage_gaps"])
        task = next(t for t in plan["tasks"] if t["aspect"] == "correctness")
        for t in plan["tasks"]:
            if t["stage"] == "essential":
                self.receipt(t, [self.finding(t)] if t == task else [])
        unverified = self.outcome("report")
        self.assertEqual(unverified["gate"], "pending")
        self.assertFalse(unverified["findings"])
        self.assertTrue(any("Unverified candidate" in g["reason"] for g in unverified["coverage_gaps"]))
        verification = self.verification(task)
        schedule = self.outcome("next", verification)
        self.assertEqual(schedule["gate"], "blocked")
        self.assertEqual(schedule["skipped"], [t["id"] for t in plan["tasks"] if t["stage"] == "optional"])
        report = self.outcome("report", verification)
        self.assertEqual((report["base"], report["head"]), (self.base, self.head))
        self.assertEqual(report["findings"][0]["id"], task["id"] + ":f1")
        self.assertEqual(report["coverage_gaps"], [])
        self.assertFalse(report["improvements"])

    def test_clean_essentials_allow_verified_optional_improvement(self):
        plan = self.prepare()
        for task in plan["tasks"]:
            if task["stage"] == "essential":
                self.receipt(task)
        optional = next(t for t in plan["tasks"] if t["stage"] == "optional")
        schedule = self.outcome("next")
        self.assertEqual(schedule["gate"], "clean")
        self.assertEqual(schedule["launch"], [optional["id"]])
        self.receipt(optional, [self.finding(optional, "improvement")])
        report = self.outcome("report", self.verification(optional))
        self.assertEqual(report["gate"], "clean")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["coverage_gaps"], [])
        self.assertEqual(report["improvements"][0]["id"], optional["id"] + ":f1")

    def test_binary_changes_remain_declared_gaps_in_report(self):
        (self.repo / "asset.bin").write_bytes(b"\0binary fixture\xff")
        self.head = commit(self.repo)
        self.cli("inventory", "--repo", self.repo, "--base", self.base, "--head", self.head,
                 "--out", self.bundle)
        self.plan_path = self.root / "plan.json"
        self.plan_record = self.cli("plan", "--bundle", self.bundle, "--out", self.plan_path)
        self.results_dir = self.root / "results"
        self.results_dir.mkdir()
        self.assertTrue(self.plan_record["gaps"])
        for task in self.plan_record["tasks"]:
            self.receipt(task)
        report = self.outcome("report")
        self.assertEqual(report["gate"], "blocked")
        self.assertEqual(report["coverage_gaps"], self.plan_record["gaps"])
        self.assertFalse(report["findings"])

    def test_stale_receipt_fails_with_structured_incomplete_error(self):
        plan = self.prepare()
        path = self.receipt(plan["tasks"][0])
        receipt = json.loads(path.read_text())
        receipt["input_hash"] = "stale-input"
        self.write_json(path, receipt)
        error = self.outcome("report", expected=2)
        self.assertEqual(error, {"error": "stale task receipt", "status": "incomplete"})
