"""Fresh-install CLI integration with synthetic receipts, without inference."""
import json
import shutil
import subprocess
import sys

from support import GitCase, ROOT, SKILL, receipts
from reviewlib.records import save


class InstallationTests(GitCase):
    def exercise_installation(self, plugin):
        install = self.root / "fresh install"
        if plugin:
            skill = install / "codex/skills/code-review-c"
            for relative in (".codex-plugin/plugin.json", ".agents/plugins/marketplace.json"):
                target = install / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
        else:
            skill = install / "skills/code-review-c"
        shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
        helper = skill / "scripts/review.py"
        cwd = self.root / "unrelated working directory"
        cwd.mkdir()

        def cli(*args, success=True):
            result = subprocess.run([sys.executable, str(helper), *map(str, args)], cwd=cwd,
                                    capture_output=True, text=True, timeout=30)
            if success:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            return json.loads(result.stderr)

        bundle, plan_path = self.root / "bundle", self.root / "plan.json"
        cli("inventory", "--repo", self.repo, "--base", self.base, "--head", self.head, "--out", bundle)
        plan = cli("plan", "--bundle", bundle, "--out", plan_path)
        common = ("--bundle", bundle, "--plan", plan_path)
        self.assertTrue(cli("validate", *common)["valid"])
        results = self.root / "results"
        results.mkdir()
        schedule_args = (*common, "--results", results, "--capacity", "2")
        initial = cli("next", *schedule_args)
        self.assertEqual(initial["gate"], "pending")
        self.assertEqual(len(initial["launch"]), 2)
        self.assertTrue(initial["queued"])

        for task, receipt in zip(plan["tasks"], receipts(plan)):
            prompt = self.root / (task["id"] + ".txt")
            cli("prompt", *common, "--task", task["id"], "--out", prompt)
            self.assertIn("OWNED CHANGED HUNKS", prompt.read_text())
            self.assertTrue(set(task["hunks"]))
            for hunk in task["hunks"]:
                self.assertIn(hunk, prompt.read_text())
            save(results / (task["id"] + ".json"), receipt)
        report = cli("report", *common, "--results", results)
        self.assertEqual(report["gate"], "clean")
        self.assertEqual(report["coverage_gaps"], [])
        self.assertEqual(report["findings"], [])

        # Resume retained work, but reject receipts for stale task inputs.
        receipt_path = results / (plan["tasks"][0]["id"] + ".json")
        original = json.loads(receipt_path.read_text())
        receipt_path.unlink()
        resumed = cli("next", *schedule_args)
        self.assertEqual(resumed["launch"], [original["task_id"]])
        self.assertEqual(resumed["gate"], "pending")
        save(receipt_path, {**original, "input_hash": "0" * 64})
        error = cli("report", *common, "--results", results, success=False)
        self.assertIn("stale task receipt", error["error"])
        save(receipt_path, original)
        self.assertEqual(cli("report", *common, "--results", results)["gate"], "clean")

        for forbidden in (skill / "state", skill / "profiles", self.repo / "results"):
            self.assertFalse(forbidden.exists())

    def test_copied_skill_runs_from_unrelated_directory(self):
        self.exercise_installation(plugin=False)

    def test_plugin_layout_runs_from_unrelated_directory(self):
        self.exercise_installation(plugin=True)
