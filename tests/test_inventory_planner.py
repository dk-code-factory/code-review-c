import copy
import json
import subprocess
from unittest.mock import patch

from support import GitCase, SKILL, commit, git, identity, rehash
from reviewlib import inventory, planner, profiles, results
from reviewlib.records import digest, external, save


class InventoryTests(GitCase):
    def test_pins_exclude_dirty_worktree_and_moving_names(self):
        (self.repo / "a.py").write_text("DIRTY DO NOT REVIEW\n")
        inv = self.inventory()
        self.assertTrue(inv["dirty"])
        self.assertEqual(inv["head"], self.head)
        data = (self.bundle / inv["hunks"][0]["ref"]).read_text()
        self.assertIn("+    return 2", data)
        self.assertNotIn("DIRTY", data)

    def test_no_full_patch_in_initial_inventory(self):
        inv = self.inventory()
        self.assertNotIn("return 2", json.dumps(inventory.summary(inv)))
        self.assertEqual((inv["files"][0]["added"], inv["files"][0]["removed"]), (1, 1))

    def test_rename_deletion_mode_binary_and_odd_paths(self):
        git(self.repo, "reset", "--hard", self.base)
        for name in ["old.py", "delete.py", "odd\tname\n.py", "mode.py"]:
            (self.repo / name).write_text(("unchanged " + name + "\n") * 20)
        base = commit(self.repo)
        git(self.repo, "mv", "old.py", "new.py")
        (self.repo / "delete.py").unlink()
        (self.repo / "odd\tname\n.py").write_text("changed\n")
        (self.repo / "mode.py").chmod(0o755)
        (self.repo / "image.bin").write_bytes(b"\0\1\2" * 300)
        head = commit(self.repo)
        inv = inventory.build(self.repo, self.bundle, base, head)
        statuses = {f["path"]: f["status"] for f in inv["files"]}
        self.assertTrue(statuses["new.py"].startswith("R"))
        self.assertEqual(statuses["delete.py"], "D")
        self.assertIn("odd\tname\n.py", statuses)
        self.assertTrue(any(h["binary"] for h in inv["hunks"]))
        self.assertTrue(any(h["path"] == "mode.py" and h["span"] is None for h in inv["hunks"]))
        plan = planner.build(self.bundle)
        self.assertTrue(any("Binary" in g["reason"] for g in plan["gaps"]))
        planner.validate(plan, inv, self.bundle)

    def test_multiple_hunks_and_exact_raw_storage(self):
        (self.repo / "a.py").write_text("\n".join(f"line{i}" for i in range(100)) + "\n")
        base = commit(self.repo)
        lines = (self.repo / "a.py").read_text().splitlines()
        lines[3], lines[90] = "changed early", "changed late"
        (self.repo / "a.py").write_text("\n".join(lines) + "\n")
        head = commit(self.repo)
        inv = inventory.build(self.repo, self.bundle, base, head)
        self.assertEqual(len(inv["hunks"]), 2)
        for h in inv["hunks"]:
            data = (self.bundle / h["ref"]).read_bytes()
            self.assertEqual((len(data), digest(data)), (h["bytes"], h["sha256"]))
        self.assertEqual(inv["files"][0]["added"], 2)

    def test_submodule_settings_cannot_hide_changed_gitlinks(self):
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000," + self.base + ",dependency")
        git(self.repo, "commit", "-m", "Add gitlink")
        base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-index", "--cacheinfo", "160000," + self.head + ",dependency")
        git(self.repo, "commit", "-m", "Change gitlink")
        head = git(self.repo, "rev-parse", "HEAD")
        for ignore in ("all", "dirty", "untracked", "none"):
            for display in ("short", "log", "diff"):
                with self.subTest(ignore=ignore, display=display):
                    git(self.repo, "config", "diff.ignoreSubmodules", ignore)
                    git(self.repo, "config", "diff.submodule", display)
                    bundle = self.root / (ignore + "-" + display)
                    inv = inventory.build(self.repo, bundle, base, head)
                    self.assertEqual([f["path"] for f in inv["files"]], ["dependency"])
                    self.assertTrue(inv["hunks"][0]["opaque"])
                    plan = planner.build(bundle)
                    report = results.report(plan, [], None)
                    self.assertEqual(report["gate"], "blocked")
                    self.assertEqual(len(report["coverage_gaps"]), len(planner.ESSENTIAL))

    def test_renamed_gitlink_remains_an_opaque_gap(self):
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000," + self.base + ",old-dependency")
        git(self.repo, "commit", "-m", "Add gitlink")
        base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-index", "--force-remove", "old-dependency")
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000," + self.base + ",new-dependency")
        git(self.repo, "commit", "-m", "Rename gitlink")
        head = git(self.repo, "rev-parse", "HEAD")
        inv = inventory.build(self.repo, self.bundle, base, head)
        self.assertEqual(inv["files"][0]["status"], "R100")
        self.assertTrue(inv["hunks"][0]["opaque"])
        self.assertEqual(results.report(planner.build(self.bundle), [], None)["gate"], "blocked")

    def test_type_change_remains_an_opaque_gap(self):
        (self.repo / "a.py").unlink()
        (self.repo / "a.py").symlink_to("target.py")
        head = commit(self.repo)
        inv = inventory.build(self.repo, self.bundle, self.base, head)
        self.assertEqual(inv["files"][0]["status"], "T")
        self.assertTrue(all(h["opaque"] for h in inv["hunks"]))
        self.assertEqual(results.report(planner.build(self.bundle), [], None)["gate"], "blocked")

    def test_corrupt_pinned_patch_fails(self):
        inv = self.inventory()
        (self.bundle / inv["hunks"][0]["ref"]).write_text("tampered")
        with self.assertRaisesRegex(ValueError, "pinned hunk"):
            inventory.read(self.bundle)

    def test_private_output_inside_checkout_rejected(self):
        with self.assertRaises(ValueError):
            inventory.build(self.repo, self.repo / "private", self.base, self.head)
        with self.assertRaises(ValueError):
            external(SKILL / "private")

    def test_pr_metadata_and_merge_base_pinned(self):
        metadata = {"number": 7, "url": "https://github.com/org/repo/pull/7", "title": "Pinned", "body": "Acceptance",
                    "baseRefOid": self.base, "headRefOid": self.head, "commits": [], "files": []}
        with patch.object(inventory, "pin_pr", return_value=metadata):
            inv = inventory.build(self.repo, self.bundle, pr="7")
        self.assertEqual(inv["pr"], metadata)
        self.assertEqual(inv["diff_base"], git(self.repo, "merge-base", self.base, self.head))

    def test_invalid_and_ambiguous_revisions_fail(self):
        with self.assertRaises(subprocess.CalledProcessError):
            inventory.build(self.repo, self.bundle, "does-not-exist", self.head)
        with self.assertRaises(ValueError):
            inventory.build(self.repo, self.bundle, self.base)


class PlanningTests(GitCase):
    def test_exact_essential_coverage_and_determinism(self):
        plan = self.plan()
        self.assertEqual(plan, planner.build(self.bundle))
        self.assertEqual(len(plan["tasks"]), len(planner.ESSENTIAL))
        self.assertEqual({t["aspect"] for t in plan["tasks"]}, set(planner.ESSENTIAL))
        self.assertFalse(plan["gaps"])

    def test_rendered_accounting_includes_every_byte(self):
        plan = self.plan()
        for task in plan["tasks"]:
            text, accounting = planner.render(task, self.inv, self.bundle)
            self.assertEqual(accounting["total"], len(text.encode()) + task["host_input_bytes"])
            self.assertEqual(accounting["total"], sum(v for k, v in accounting.items() if k != "total"))
            self.assertEqual(task["input_hash"], digest(text.encode()))
            self.assertIn(f'INITIAL_TOTAL_VISIBLE_BYTES={accounting["total"]:012d}', text)
            self.assertIn(f'REMAINING_VISIBLE_BYTES={task["limits"]["total_visible_bytes"] - accounting["total"]:012d}', text)

    def test_non_utf8_patch_accounted_as_displayed(self):
        (self.repo / "a.py").write_bytes(b"# \xff\xfe\n")
        self.head = commit(self.repo)
        plan = self.plan()
        task = plan["tasks"][0]
        text, accounting = planner.render(task, self.inv, self.bundle)
        self.assertIn("\\xff", text)
        self.assertGreater(accounting["patch"], sum(h["bytes"] for h in self.inv["hunks"]))

    def test_oversized_indivisible_hunk_becomes_gap(self):
        (self.repo / "a.py").write_text("# " + "x" * 80000 + "\n")
        self.head = commit(self.repo)
        plan = self.plan()
        self.assertFalse(plan["tasks"])
        self.assertEqual(len(plan["gaps"]), len(planner.ESSENTIAL))

    def test_budget_splits_files_without_dropping_coverage(self):
        for n in range(3):
            (self.repo / f"f{n}.py").write_text("# " + "x" * 25000 + "\n")
        self.head = commit(self.repo)
        plan = self.plan()
        self.assertGreater(len(plan["tasks"]), len(planner.ESSENTIAL))
        self.assertFalse(plan["gaps"])
        self.assertTrue(all(t["bytes"]["total"] <= 58000 for t in plan["tasks"]))

    def test_languages_stay_separate_interface_gets_contract(self):
        (self.repo / "a.go").write_text("package api\nconst Field = 2\n")
        self.head = commit(self.repo)
        self.inv = self.inventory()
        ids = [h["id"] for h in self.inv["hunks"]]
        analysis = {"interfaces": [{"hunks": ids, "reason": "Response field crosses schema boundary",
                    "evidence": [{"revision": self.head, "path": "a.go", "start": 1, "end": 2}]}]}
        plan = planner.build(self.bundle, analysis)
        ordinary = [t for t in plan["tasks"] if not t["supplemental"]]
        self.assertTrue(all(len(t["languages"]) == 1 for t in ordinary))
        contract = next(t for t in plan["tasks"] if t["supplemental"])
        self.assertEqual(set(contract["languages"]), {"python", "go"})
        self.assertEqual(set(contract["prerequisites"]), {t["id"] for t in ordinary if t["aspect"] == "correctness"})

    def test_focused_instructions_and_optional_dependencies(self):
        plan = self.plan({"files": {"a.py": {"aspects": ["concurrency", "simplification"]}}, "optional_reason": "State duplication"})
        essential = {t["id"] for t in plan["tasks"] if t["stage"] == "essential"}
        optional = next(t for t in plan["tasks"] if t["stage"] == "optional")
        self.assertEqual(set(optional["prerequisites"]), essential)
        for t in plan["tasks"]:
            aspects = [r["ref"] for r in t["instructions"] if "/aspects/" in r["ref"]]
            self.assertEqual(aspects, [f'references/aspects/{t["aspect"]}.md'])

    def dependency_boundary(self, multiple_hunks=False, interface=False):
        if multiple_hunks:
            (self.repo / "a.py").write_text("\n".join(f"line{i}" for i in range(100)) + "\n")
            self.base = commit(self.repo)
            lines = (self.repo / "a.py").read_text().splitlines()
            lines[3], lines[90] = "changed early", "changed late"
            (self.repo / "a.py").write_text("\n".join(lines) + "\n")
            self.head = commit(self.repo)
        inv = self.inventory()
        analysis = {"files": {"a.py": {"aspects": ["simplification"]}},
                    "optional_reason": "Changed duplicate mapping"}
        if interface:
            analysis["interfaces"] = [{"hunks": [h["id"] for h in inv["hunks"]],
                "reason": "Changed caller and callee contract",
                "evidence": [{"revision": self.head, "path": "a.py", "start": 1, "end": 4}]}]
        ordinary = planner.build(self.bundle, analysis)
        task = copy.deepcopy(next(t for t in ordinary["tasks"] if t["stage"] == "optional"))
        task["prerequisites"] = []
        _, before = planner.render(task, inv, self.bundle)
        limit = before["total"] + 1
        select = profiles.select

        def boundary_profile(directory, identity, languages, domain, aspect):
            profile = select(directory, identity, languages, domain, aspect)
            if aspect == "simplification":
                profile["limits"]["total_visible_bytes"] = limit
            return profile

        with patch.object(profiles, "select", side_effect=boundary_profile):
            plan = planner.build(self.bundle, analysis)
            self.assertEqual(plan, planner.build(self.bundle, analysis))
        planner.validate(plan, inv, self.bundle)
        return plan, limit

    def test_dependency_bytes_turn_indivisible_optional_hunk_into_gap(self):
        plan, limit = self.dependency_boundary()
        self.assertFalse(any(t["stage"] == "optional" for t in plan["tasks"]))
        self.assertEqual([g["aspects"] for g in plan["gaps"]], [["simplification"]])
        self.assertEqual(len(plan["tasks"]), len(planner.ESSENTIAL))

    def test_dependency_bytes_split_optional_hunks_with_contract_dependency(self):
        plan, limit = self.dependency_boundary(multiple_hunks=True, interface=True)
        optional = [t for t in plan["tasks"] if t["stage"] == "optional"]
        essential = {t["id"] for t in plan["tasks"] if t["stage"] == "essential"}
        self.assertEqual(len(optional), 2)
        self.assertFalse(plan["gaps"])
        self.assertTrue(any(t["supplemental"] for t in plan["tasks"]))
        for task in optional:
            self.assertEqual(set(task["prerequisites"]), essential)
            self.assertLessEqual(task["bytes"]["total"], limit)

    def test_unknown_paths_aspects_and_unpinned_context_fail(self):
        self.inventory()
        for analysis in ({"files": {"missing.py": {}}}, {"files": {"a.py": {"aspects": ["fashion"]}}},
                         {"files": {"a.py": {"evidence": [{"revision": "HEAD", "path": "a.py", "start": 1, "end": 2}]}}}):
            with self.subTest(analysis=analysis), self.assertRaises(ValueError):
                planner.build(self.bundle, analysis)

    def test_missing_and_duplicate_coverage_rejected(self):
        plan = self.plan()
        for duplicate in (False, True):
            broken = copy.deepcopy(plan)
            broken["tasks"] = broken["tasks"] + [copy.deepcopy(broken["tasks"][0])] if duplicate else broken["tasks"][1:]
            if duplicate:
                broken["tasks"][-1]["id"] = "other"
                t = broken["tasks"][-1]
                text, t["bytes"] = planner.render(t, self.inv, self.bundle)
                t["input_hash"] = digest(text.encode())
            with self.subTest(duplicate=duplicate), self.assertRaises(ValueError):
                planner.validate(rehash(broken), self.inv, self.bundle)

    def test_cyclic_dependencies_fail(self):
        plan = self.plan()
        plan["tasks"][0]["prerequisites"] = [plan["tasks"][1]["id"]]
        plan["tasks"][1]["prerequisites"] = [plan["tasks"][0]["id"]]
        for t in plan["tasks"]:
            text, t["bytes"] = planner.render(t, self.inv, self.bundle)
            t["input_hash"] = digest(text.encode())
        with self.assertRaisesRegex(ValueError, "cyclic"):
            planner.validate(rehash(plan), self.inv, self.bundle)

    def test_evidence_expansion_within_and_beyond_budget(self):
        (self.repo / "context.py").write_text("# " + "x" * 70000 + "\n")
        self.head = commit(self.repo)
        plan = self.plan()
        t = plan["tasks"][0]
        old_id = plan["id"]
        small = {"revision": self.head, "path": "a.py", "start": 1, "end": 2}
        expanded = planner.expand(copy.deepcopy(plan), self.bundle, t["id"], small)
        self.assertNotEqual(expanded["id"], old_id)
        huge = {"revision": self.head, "path": "context.py", "start": 1, "end": 1}
        with self.assertRaisesRegex(ValueError, "exceeds budget"):
            planner.expand(copy.deepcopy(plan), self.bundle, t["id"], huge)

    def test_model_identity_and_stale_hash_fall_back(self):
        self.inventory()
        old = identity()
        old["instruction_hash"] = "old"
        plan = planner.build(self.bundle, identity=old)
        self.assertIsNone(plan["identity"])
        self.assertTrue(all(t["profile"] == "provisional" for t in plan["tasks"]))
        profile_dir = self.root / "profiles"
        profile_dir.mkdir()
        (profile_dir / "broken.json").write_text("{invalid")
        self.assertEqual(profiles.select(profile_dir, identity(), ["python"], "backend", "correctness")["id"], "provisional")
