import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from support import GitCase, commit, git, identity, synthetic_trials
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

    def test_freeze_rejects_unchanged_nested_submodule(self):
        spec = self.campaign_spec()
        fixture = spec["fixtures"][0]
        repo = Path(fixture["repo"])
        pin = fixture["base"]
        for revision in ("base", "head"):
            git(repo, "checkout", "--detach", fixture[revision])
            git(repo, "update-index", "--add", "--cacheinfo", "160000", pin, "nested/module")
            git(repo, "commit", "-m", "Include unchanged submodule")
            fixture[revision] = git(repo, "rev-parse", "HEAD")
        fixture["evidence"][0]["revision"] = fixture["head"]
        save(self.root / "spec.json", spec)
        with self.assertRaisesRegex(ValueError, "submodule"):
            onboarding.freeze(self.root / "spec.json", self.root / "bad")

    def test_freeze_preserves_raw_files_despite_export_attributes(self):
        spec = self.campaign_spec()
        fixture = spec["fixtures"][0]
        repo = Path(fixture["repo"])
        name = "dependencies/odd\nname-\udcff.bin"
        for revision in ("base", "head"):
            git(repo, "checkout", "--detach", fixture[revision])
            (repo / ".gitattributes").write_text("dependencies export-ignore\nstamp.py export-subst\n")
            (repo / name).parent.mkdir(exist_ok=True)
            (repo / name).write_bytes(b"\x00\xffraw dependency\n")
            (repo / "stamp.py").write_bytes(b"$Format:%H$\n")
            (repo / "tool.sh").write_text("#!/bin/sh\nexit 0\n")
            (repo / "tool.sh").chmod(0o755)
            fixture[revision] = commit(repo)
        fixture["evidence"][0]["revision"] = fixture["head"]
        save(self.root / "spec.json", spec)
        output = self.root / "attributed"
        onboarding.freeze(self.root / "spec.json", output)
        for revision in ("base", "head"):
            tree = output / "fixtures/train0/workspace" / revision
            with self.subTest(revision=revision, attribute="export-ignore"):
                self.assertTrue((tree / name).is_file())
                self.assertEqual((tree / name).read_bytes(), b"\x00\xffraw dependency\n")
            with self.subTest(revision=revision, attribute="export-subst"):
                self.assertEqual((tree / "stamp.py").read_bytes(), b"$Format:%H$\n")
            self.assertTrue((tree / "tool.sh").stat().st_mode & 0o111)

    def test_freeze_from_repository_subdirectory_preserves_full_pinned_trees(self):
        spec = self.campaign_spec()
        fixture = spec["fixtures"][0]
        repo = Path(fixture["repo"])
        nested = repo / "nested"
        nested.mkdir()
        fixture["repo"] = str(nested)
        save(self.root / "spec.json", spec)
        output = self.root / "nested-campaign"
        campaign = onboarding.freeze(self.root / "spec.json", output)
        for revision in ("base", "head"):
            tree = output / "fixtures/train0/workspace" / revision
            self.assertEqual({p.name for p in tree.iterdir()}, {"context.py", "f0.py"})
            for name in ("context.py", "f0.py"):
                expected = subprocess.check_output(["git", "-C", str(repo), "show", fixture[revision] + ":" + name])
                self.assertEqual((tree / name).read_bytes(), expected)
        self.assertEqual(onboarding.read(output), campaign)

    def test_archive_rejects_symlinks(self):
        (self.repo / "link").symlink_to("a.py")
        revision = commit(self.repo)
        with self.assertRaisesRegex(ValueError, "symlink"):
            onboarding.archive(self.repo, revision, self.root / "linked")

    def test_frozen_tree_executable_mode_drift_rejected(self):
        spec = self.campaign_spec()
        fixture = spec["fixtures"][0]
        repo = Path(fixture["repo"])
        for revision in ("base", "head"):
            git(repo, "checkout", "--detach", fixture[revision])
            (repo / "f0.py").chmod(0o755)
            fixture[revision] = commit(repo)
        fixture["evidence"][0]["revision"] = fixture["head"]
        save(self.root / "spec.json", spec)
        output = self.root / "executable"
        campaign = onboarding.freeze(self.root / "spec.json", output)
        path = output / "fixtures/train0/workspace/head/f0.py"
        for mode in (0o655, 0o644):
            with self.subTest(mode=oct(mode)):
                path.chmod(mode)
                with self.assertRaisesRegex(ValueError, "fixture trees changed"):
                    onboarding.read(output)
                path.chmod(0o755)
                self.assertEqual(onboarding.read(output), campaign)

    def test_frozen_tree_rejects_same_content_file_symlink(self):
        campaign = self.freeze()
        self.assertEqual(onboarding.read(self.croot), campaign)
        path = self.croot / "fixtures/train0/workspace/head/f0.py"
        target = self.root / "external.py"
        shutil.copy2(path, target)
        path.unlink()
        path.symlink_to(target)
        try:
            with self.assertRaisesRegex(ValueError, "regular files and directories"):
                onboarding.read(self.croot)
        finally:
            path.unlink()
            shutil.copy2(target, path)
        self.assertEqual(onboarding.read(self.croot), campaign)

    def test_frozen_tree_rejects_unsupported_extra_nodes(self):
        campaign = self.freeze()
        workspace = self.croot / "fixtures/train0/workspace"
        target = self.root / "external-directory"
        target.mkdir()
        for kind in ("directory-symlink", "dangling-symlink", "fifo"):
            with self.subTest(kind=kind):
                node = workspace / "unsupported"
                if kind == "directory-symlink":
                    node.symlink_to(target, target_is_directory=True)
                elif kind == "dangling-symlink":
                    node.symlink_to(self.root / "missing")
                else:
                    os.mkfifo(node)
                try:
                    with self.assertRaisesRegex(ValueError, "regular files and directories"):
                        onboarding.read(self.croot)
                finally:
                    node.unlink()
                self.assertEqual(onboarding.read(self.croot), campaign)

    def test_frozen_workspace_root_symlink_rejected(self):
        campaign = self.freeze()
        workspace = self.croot / "fixtures/train0/workspace"
        target = self.root / "saved-workspace"
        workspace.rename(target)
        workspace.symlink_to(target, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "regular files and directories"):
                onboarding.read(self.croot)
        finally:
            workspace.unlink()
            target.rename(workspace)
        self.assertEqual(onboarding.read(self.croot), campaign)

    def test_frozen_tree_directory_additions_and_removals_rejected(self):
        spec = self.campaign_spec()
        fixture = spec["fixtures"][0]
        repo = Path(fixture["repo"])
        git(repo, "rm", "-r", "--", ".")
        fixture["base"] = commit(repo)
        (repo / "f0.py").write_text("def f0():\n    return 1\n")
        fixture["head"] = commit(repo)
        fixture["evidence"] = []
        save(self.root / "spec.json", spec)
        output = self.root / "first-file"
        campaign = onboarding.freeze(self.root / "spec.json", output)
        workspace = output / "fixtures/train0/workspace"
        self.assertFalse(any((workspace / "base").iterdir()))
        self.assertEqual(onboarding.read(output), campaign)
        for change in ("remove-base", "add-empty"):
            with self.subTest(change=change):
                path = workspace / ("base" if change == "remove-base" else "added-empty")
                if change == "remove-base":
                    path.rmdir()
                else:
                    path.mkdir()
                try:
                    with self.assertRaisesRegex(ValueError, "fixture trees changed"):
                        onboarding.read(output)
                finally:
                    if change == "remove-base":
                        path.mkdir()
                    else:
                        path.rmdir()
                self.assertEqual(onboarding.read(output), campaign)

    def test_freeze_rejects_invalid_host_input_reservations(self):
        spec = self.campaign_spec()
        for index, value in enumerate((-1, True, 1.5, "8000", None)):
            with self.subTest(host_input_bytes=value):
                spec["fixtures"][0]["host_input_bytes"] = value
                save(self.root / "spec.json", spec)
                with self.assertRaisesRegex(ValueError, "host.*nonnegative integer"):
                    onboarding.freeze(self.root / "spec.json", self.root / f"bad-{index}")
        spec["fixtures"][0]["host_input_bytes"] = 0
        save(self.root / "spec.json", spec)
        campaign = onboarding.freeze(self.root / "spec.json", self.root / "zero")
        self.assertEqual(campaign["fixtures"][0]["prompts"]["focused"]["bytes"]["host"], 0)

    def test_freeze_rejects_scope_languages_without_fixture_evidence(self):
        spec = self.campaign_spec()
        spec["scope"]["languages"].append("rust")
        save(self.root / "spec.json", spec)
        with self.assertRaisesRegex(ValueError, "scope language.*fixture"):
            onboarding.freeze(self.root / "spec.json", self.root / "bad")

    def test_freeze_accepts_scope_language_union_across_fixtures(self):
        spec = self.campaign_spec()
        fixture = self.fixture("go")
        repo = Path(fixture["repo"])
        for revision in ("base", "head"):
            git(repo, "checkout", "--detach", fixture[revision])
            git(repo, "mv", "f0.py", "f0.go")
            fixture[revision] = commit(repo)
        fixture["evidence"][0]["revision"] = fixture["head"]
        spec["fixtures"].append(fixture)
        spec["scope"]["languages"] = ["python", "go"]
        save(self.root / "spec.json", spec)
        campaign = onboarding.freeze(self.root / "spec.json", self.root / "multilanguage")
        self.assertEqual([f["languages"] for f in campaign["fixtures"]], [["python"], ["go"]])
        self.assertEqual(campaign["scope"]["languages"], ["go", "python"])

    def test_gold_key_drift_rejected(self):
        self.freeze()
        path = self.croot / "grading/keys.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "answer keys changed"):
            onboarding.read(self.croot)

    def test_frozen_prompt_drift_rejected_independently(self):
        campaign = self.freeze()
        path = self.croot / campaign["fixtures"][0]["prompts"]["focused"]["ref"]
        original = path.read_bytes()
        path.write_bytes(original + b"\nChanged prompt\n")
        with self.assertRaisesRegex(ValueError, "fixture prompt changed"):
            onboarding.read(self.croot)
        path.write_bytes(original)
        self.assertEqual(onboarding.read(self.croot), campaign)

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

    def test_promotion_rejects_output_inside_every_fixture_repository(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        for fixture in load(self.root / "spec.json")["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                output = Path(fixture["repo"]) / "private-profiles"
                with self.assertRaisesRegex(ValueError, "outside repository/package"):
                    onboarding.promote(self.croot, "focused", output)
                self.assertFalse(output.exists())

    def test_three_matched_repetitions_mandatory(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        shutil.rmtree(self.croot / "attempts/train0--focused--2")
        report = onboarding.compare(self.croot)
        self.assertIn("train0--focused--2", report["missing"])
        with self.assertRaises(ValueError):
            onboarding.promote(self.croot, "focused", self.root / "profiles")

    def test_incomplete_matched_control_blocks_campaign_promotion(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        trial = self.croot / "attempts/train0--combined--0"
        original = {name: load(trial / (name + ".json")) for name in ("receipt", "result", "decisions")}
        for case in ("failed", "incomplete", "unmeasured", "unresolved"):
            with self.subTest(case=case):
                receipt, result, decisions = (copy.deepcopy(original[name]) for name in ("receipt", "result", "decisions"))
                if case == "failed":
                    receipt["status"] = "failed"
                elif case == "incomplete":
                    result["status"] = "incomplete"
                elif case == "unmeasured":
                    receipt["elapsed_seconds"] = 0
                else:
                    decisions["decisions"][0]["disposition"] = "unresolved"
                save(trial / "result.json", result)
                receipt["result_hash"] = digest((trial / "result.json").read_bytes())
                save(trial / "receipt.json", receipt)
                save(trial / "decisions.json", decisions)
                onboarding.grade(self.croot, trial.name, trial / "decisions.json")
                report = onboarding.compare(self.croot)
                self.assertIn(trial.name, report["missing"])
                self.assertFalse(report["qualifying"])
                with self.assertRaisesRegex(ValueError, "blocks promotion"):
                    onboarding.promote(self.croot, "focused", self.root / "profiles")

    def test_measured_losing_control_remains_valid_comparison_evidence(self):
        self.freeze(full=True)
        synthetic_trials(self.croot, self.c)
        trial = self.croot / "attempts/train0--combined--0"
        save(trial / "decisions.json", record("grading-decisions", decisions=[
            {"finding": "f1", "disposition": "false-positive", "evidence": ["Measured control missed the gold defect"]}]))
        onboarding.grade(self.croot, trial.name, trial / "decisions.json")
        report = onboarding.compare(self.croot)
        self.assertTrue(report["qualifying"])
        self.assertFalse(next(s for s in report["strategies"] if s["id"] == "combined")["passing"])
        self.assertEqual(onboarding.promote(self.croot, "focused", self.root / "profiles")["strategy"], "focused")

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

    def test_incompatible_native_campaign_retained_only_as_history(self):
        self.freeze()
        for difference in ("instructions", "campaign"):
            with self.subTest(difference=difference):
                old = copy.deepcopy(self.c)
                if difference == "instructions":
                    old["identity"]["instruction_hash"] = "0" * 64
                else:
                    old["scope"]["domain"] = "generic"
                old["id"] = digest({k: v for k, v in old.items() if k != "id"})
                source = self.root / ("old-" + difference)
                save(source / "campaign.json", old)
                result = onboarding.import_evidence(self.croot, source, "native")
                self.assertEqual(result["compatibility"], "historical only")
                historical = load(self.croot / "historical" / (digest(old) + ".json"))
                self.assertFalse(historical["compatible"])
                self.assertEqual(historical["format"], "native")
                self.assertEqual(historical["source"], str(source.resolve()))
                self.assertEqual(historical["data"], old)
                self.assertFalse((self.croot / "attempts").exists())
        self.assertFalse(onboarding.compare(self.croot)["qualifying"])
        with self.assertRaisesRegex(ValueError, "blocks promotion"):
            onboarding.promote(self.croot, "focused", self.root / "profiles")

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

    def test_exact_native_import_rejects_corruption_without_historical_fallback(self):
        self.freeze()
        synthetic_trials(self.croot, self.c)
        copy_root = self.root / "copy"
        shutil.copytree(self.croot, copy_root)
        shutil.rmtree(copy_root / "attempts")
        prompt = self.croot / self.c["fixtures"][0]["prompts"]["focused"]["ref"]
        prompt.write_bytes(prompt.read_bytes() + b"\nCorrupt exact-match source\n")
        with self.assertRaisesRegex(ValueError, "fixture prompt changed"):
            onboarding.import_evidence(copy_root, self.croot, "native")
        self.assertFalse((copy_root / "attempts").exists())
        self.assertFalse((copy_root / "historical").exists())

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

    def fake_runner(self, timeout=False, interrupt=False, expected_launches=1):
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
                self.assertEqual(len(cleanups), expected_launches + 1)
        self.assertEqual(result["launched"], expected_launches)
        self.assertEqual(len(calls), expected_launches)
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

    def test_resume_preserves_uncertain_attempt_time_reservations(self):
        self.freeze()
        receipt = self.fake_runner()
        path = self.croot / "attempts/train0--focused--0/receipt.json"
        cases = [(status, reserved, elapsed, 0)
                 for status in ("started", "interrupted", "cleanup-failed")
                 for reserved, elapsed in ((10000, 1), (1, 10000))]
        cases += [(status, 10000, 1, 1) for status in ("finished", "failed", "timeout")]
        for status, reserved, elapsed, launches in cases:
            with self.subTest(status=status, reserved=reserved, elapsed=elapsed):
                shutil.rmtree(self.croot / "attempts")
                retained = {**receipt, "status": status, "reserved_seconds": reserved, "elapsed_seconds": elapsed}
                save(path, retained)
                self.fake_runner(expected_launches=launches)
                self.assertEqual(load(path), retained)

    def test_version_probe_cleans_up_after_timeout_and_interrupt(self):
        self.freeze()
        config = self.root / "host.toml"
        config.write_text('model = "synthetic-exact-model"\nmodel_reasoning_effort = "high"\n')
        for error in (subprocess.TimeoutExpired("docker", 30), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), \
                 patch.object(onboarding.shutil, "which", return_value="/fake/docker"), \
                 patch.object(onboarding.subprocess, "check_output", side_effect=error) as probe, \
                 patch.object(onboarding.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as cleanup:
                with self.assertRaises(type(error)):
                    onboarding.run(self.croot, "example/codex@sha256:" + "a" * 64, config, "unused", 1, 10, 5)
                cleanup.assert_called_once()
                command = probe.call_args.args[0]
                name = command[command.index("--name") + 1]
                self.assertTrue(name.startswith("crc-version-"))
                self.assertIn("--network=none", command)
                cleanup.assert_called_once_with(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, timeout=15)
                self.assertFalse((self.croot / "attempts").exists())

    def test_version_probe_cleanup_failure_reports_exact_container(self):
        self.freeze()
        config = self.root / "host.toml"
        config.write_text('model = "synthetic-exact-model"\nmodel_reasoning_effort = "high"\n')
        auth = self.root / "auth.json"
        auth.write_text('{"synthetic": true}')
        names = []
        for outcome in (subprocess.CompletedProcess([], 1), OSError("cleanup unavailable"), subprocess.TimeoutExpired("docker rm", 15)):
            with self.subTest(outcome=type(outcome).__name__), \
                 patch.object(onboarding.shutil, "which", return_value="/fake/docker"), \
                 patch.object(onboarding.subprocess, "check_output", return_value="codex-cli synthetic\n") as probe, \
                 patch.object(onboarding.subprocess, "run", side_effect=outcome if isinstance(outcome, BaseException) else None,
                              return_value=outcome) as cleanup:
                with self.assertRaisesRegex(ValueError, "cleanup failed.*crc-version-") as raised:
                    onboarding.run(self.croot, "example/codex@sha256:" + "a" * 64, config, auth, 1, 10, 5)
                command = probe.call_args.args[0]
                name = command[command.index("--name") + 1]
                names.append(name)
                self.assertIn(name, str(raised.exception))
                cleanup.assert_called_once()
                self.assertFalse((self.croot / "attempts").exists())
        self.assertEqual(len(set(names)), len(names))

    def test_answer_key_and_scope_identifiers_cannot_collide(self):
        spec = self.campaign_spec()
        spec["fixtures"][0]["id"] = "a--b"
        save(self.root / "spec.json", spec)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            onboarding.freeze(self.root / "spec.json", self.root / "bad")
