import copy
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from support import GitCase, identity, synthetic_trials
from reviewlib import onboarding, profiles
from reviewlib.records import digest, load, record, save


class OnboardingTests(GitCase):
    def freeze(self, full=False):
        self.campaign_spec(full)
        self.croot = self.root / "campaign"
        self.c = onboarding.freeze(self.root / "spec.json", self.croot)
        return self.c

    def test_prepare_launches_no_models_and_has_zero_limits(self):
        with patch("subprocess.run", side_effect=AssertionError("No subprocess")):
            spec = onboarding.prepare(self.root / "prepare")
        self.assertFalse(spec["fixtures"])
        self.assertEqual(spec["limits"]["max_calls"], 0)
        self.assertTrue((self.root / "prepare/matrix.json").exists())

    def test_freeze_hides_keys_from_trial_workspace(self):
        c = self.freeze()
        workspace = self.croot / "fixtures/train0/workspace"
        self.assertFalse(any("keys" in p.name or "spec.json" == p.name for p in workspace.rglob("*")))
        self.assertTrue((self.croot / "grading/keys.json").exists())
        self.assertNotIn("criterion", (self.croot / "fixtures/train0/focused.txt").read_text())
        self.assertEqual(onboarding.read(self.croot), c)

    def test_freeze_rejects_mislabeled_measurable_dimensions(self):
        spec = self.campaign_spec()
        spec["fixtures"][0]["dimensions"]["patch_size"] = "above"
        save(self.root / "spec.json", spec)
        with self.assertRaisesRegex(ValueError, "measured"):
            onboarding.freeze(self.root / "spec.json", self.root / "bad")

    def test_gold_and_prompt_drift_rejected(self):
        self.freeze()
        path = self.croot / "grading/keys.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "answer keys changed"):
            onboarding.read(self.croot)

    def test_missing_measurements_block_promotion(self):
        self.freeze()
        report = onboarding.compare(self.croot)
        self.assertEqual(len(report["missing"]), 6)
        self.assertFalse(report["qualifying"])
        with self.assertRaisesRegex(ValueError, "blocks promotion"):
            onboarding.promote(self.croot, "focused", self.root / "profiles")

    def test_small_campaign_does_not_establish_capacity(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        report = onboarding.compare(self.croot)
        self.assertFalse(report["qualifying"])
        self.assertFalse(report["matrix_complete"])
        self.assertFalse(report["heldout_separate"])

    def test_full_synthetic_campaign_promotes_measured_limits(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        report = onboarding.compare(self.croot)
        self.assertTrue(report["qualifying"])
        self.assertTrue(all(s["passing"] for s in report["strategies"]))
        p = onboarding.promote(self.croot, "focused", self.root / "profiles")
        self.assertLess(p["limits"]["total_visible_bytes"], 200000)
        self.assertEqual(p["limits"]["file_count"], 10)
        selected = profiles.select(self.root / "profiles", identity(), ["python"], "backend", "correctness")
        self.assertEqual(selected["id"], p["id"])
        mismatch = identity()
        mismatch["model"] = "another-model"
        self.assertEqual(profiles.select(self.root / "profiles", mismatch, ["python"], "backend", "correctness")["id"], "provisional")
        (self.root / "profiles" / p["evidence"]["report"]).write_text("{}")
        self.assertEqual(profiles.select(self.root / "profiles", identity(), ["python"], "backend", "correctness")["id"], "provisional")

    def test_three_matched_repetitions_mandatory(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        shutil.rmtree(self.croot / "attempts/train0--focused--2")
        report = onboarding.compare(self.croot)
        self.assertIn("train0--focused--2", report["missing"])
        with self.assertRaises(ValueError):
            onboarding.promote(self.croot, "focused", self.root / "profiles")

    def test_critical_miss_and_false_positive_preserved(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        trial = self.croot / "attempts/train0--focused--0"
        decisions = record("grading-decisions", decisions=[{"finding": "f1", "disposition": "false-positive", "evidence": ["Does not demonstrate gold obligation"]}])
        save(trial / "decisions.json", decisions)
        grade = onboarding.grade(self.croot, trial.name, trial / "decisions.json")
        self.assertEqual(grade["critical_misses"], ["g1"])
        self.assertEqual(grade["false_positives"], ["f1"])
        self.assertEqual(grade["recall"], 0)
        self.assertFalse(next(s for s in onboarding.compare(self.croot)["strategies"] if s["id"] == "focused")["passing"])

    def test_forged_score_does_not_replace_grading_evidence(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        path = self.croot / "attempts/train0--focused--0/grade.json"
        g = load(path)
        g["precision"] = 0.5
        save(path, g)
        self.assertIn("train0--focused--0", onboarding.compare(self.croot)["missing"])

    def test_malformed_calibration_findings_cannot_be_graded_or_compared(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        trial = self.croot / "attempts/train0--focused--0"
        result = load(trial / "result.json")
        del result["findings"][0]["hunks"]
        save(trial / "result.json", result)
        with self.assertRaisesRegex(ValueError, "missing output fields"):
            onboarding.grade(self.croot, trial.name, trial / "decisions.json")
        self.assertIn(trial.name, onboarding.compare(self.croot)["missing"])

    def test_unresolved_and_incomplete_results_fail_completion(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        trial = self.croot / "attempts/train0--focused--0"
        save(trial / "decisions.json", record("grading-decisions", decisions=[{"finding": "f1", "disposition": "unresolved", "evidence": ["Missing counterexample"]}]))
        onboarding.grade(self.croot, trial.name, trial / "decisions.json")
        score = next(s for s in onboarding.compare(self.croot)["strategies"] if s["id"] == "focused")
        self.assertFalse(score["complete"])

    def test_missing_usage_is_not_zero_cost(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        path = self.croot / "attempts/train0--focused--0/trace.jsonl"
        path.write_text(json.dumps({"type": "thread.started"}) + "\n")
        self.assertIn("train0--focused--0", onboarding.compare(self.croot)["missing"])

    def test_historical_import_does_not_enable_capacity(self):
        self.freeze()
        source = self.root / "old.json"
        save(source, {"model": "old", "languages": ["cpp", "go", "rust"], "summary": "Previous trial"})
        result = onboarding.import_evidence(self.croot, source, "skill-bench")
        self.assertEqual(result["compatibility"], "historical only")
        self.assertFalse(onboarding.compare(self.croot)["qualifying"])

    def test_exact_native_import_reuses_without_model_calls(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        copy_root = self.root / "copy"
        shutil.copytree(self.croot, copy_root)
        shutil.rmtree(copy_root / "attempts")
        with patch("subprocess.run", side_effect=AssertionError("No model calls")):
            r = onboarding.import_evidence(copy_root, self.croot, "native")
        self.assertEqual(r["imported"], 6)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            onboarding.import_evidence(copy_root, self.croot, "native")

    def test_paid_runner_requires_explicit_limits_and_immutable_image(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError, "pinned"):
            onboarding.run(self.croot, "codex:latest", "unused", "unused", 1, 10, 5)
        with self.assertRaisesRegex(ValueError, "explicit positive"):
            onboarding.run(self.croot, "example/codex@sha256:" + "a" * 64, "unused", "unused", 0, 10, 5)

    def test_combined_layout_cannot_be_promoted(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        with self.assertRaisesRegex(ValueError, "combined controls"):
            onboarding.promote(self.croot, "combined", self.root / "profiles")

    def fake_runner(self, timeout=False, interrupt=False):
        config = self.root / "host.toml"
        config.write_text('model = "synthetic-exact-model"\nmodel_reasoning_effort = "high"\n')
        auth = self.root / "auth.json"
        auth.write_text('{"synthetic": true}')
        calls = []
        cleanups = []

        def run(command, **kwargs):
            if command[1] == "rm":
                cleanups.append(command)
                return subprocess.CompletedProcess(command, 0)
            calls.append(command)
            self.assertIn("--interactive", command)
            self.assertIn("--ephemeral", command)
            self.assertIn("read-only", command)
            self.assertNotIn("--model", command)
            self.assertNotIn("-m", command)
            mounts = [command[i + 1] for i, item in enumerate(command) if item == "--mount"]
            self.assertFalse(any("grading" in mount for mount in mounts))
            output = next(m for m in mounts if "dst=/output" in m).split(",src=")[1].split(",dst=")[0]
            prompt = kwargs["input"].decode()
            self.assertIn("REMAINING_VISIBLE_BYTES=", prompt)
            kwargs["stdout"].write((json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}) + "\n").encode())
            if interrupt:
                raise KeyboardInterrupt
            if timeout:
                raise subprocess.TimeoutExpired(command, 0.01)
            save(Path(output) / "result.json", record("calibration-result", status="incomplete", inspected_hunks=[], extra_visible_bytes=0, findings=[], gaps=["Synthetic run"]))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(onboarding.shutil, "which", return_value="/fake/docker"), \
             patch.object(onboarding.subprocess, "check_output", return_value="codex-cli synthetic\n"), \
             patch.object(onboarding.subprocess, "run", side_effect=run):
            try:
                result = onboarding.run(self.croot, "example/codex@sha256:" + "a" * 64, config, auth, 1, 10, 5)
            finally:
                self.assertEqual(len(cleanups), 1)
        self.assertEqual(result["launched"], 1)
        self.assertEqual(len(calls), 1)
        return load(self.croot / "attempts/train0--focused--0/receipt.json")

    def test_runner_captures_trace_and_enforces_call_limit_without_models(self):
        self.freeze()
        receipt = self.fake_runner()
        self.assertEqual(receipt["tokens"], 120)
        self.assertTrue(receipt["trace_hash"])
        self.assertTrue(receipt["result_hash"])
        self.assertEqual(receipt["status"], "finished")

    def test_runner_timeout_retains_failed_attempt(self):
        self.freeze()
        receipt = self.fake_runner(timeout=True)
        self.assertEqual(receipt["status"], "timeout")
        self.assertTrue(receipt["launched"])
        self.assertIsNone(receipt["result_hash"])

    def test_runner_interrupt_retains_interrupted_attempt(self):
        self.freeze()
        with self.assertRaises(KeyboardInterrupt):
            self.fake_runner(interrupt=True)
        receipt = load(self.croot / "attempts/train0--focused--0/receipt.json")
        self.assertEqual(receipt["status"], "interrupted")
        self.assertTrue(receipt["launched"])

    def test_answer_key_and_scope_identifiers_cannot_collide(self):
        spec = self.campaign_spec()
        spec["fixtures"][0]["id"] = "a--b"
        save(self.root / "spec.json", spec)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            onboarding.freeze(self.root / "spec.json", self.root / "bad")
