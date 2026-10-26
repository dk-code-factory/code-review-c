import copy

from support import GitCase, candidate, identity, receipts
from reviewlib import results
from reviewlib.records import record


class ResultTests(GitCase):
    def setUp(self):
        super().setUp()
        self.plan_record = self.plan({"files": {"a.py": {"aspects": ["simplification"]}}, "optional_reason": "Duplicated mapping"})
        self.all = receipts(self.plan_record)
        self.essential = [r for r in self.all if next(t for t in self.plan_record["tasks"] if t["id"] == r["task_id"])["stage"] == "essential"]

    def verification(self, entries):
        return record("verification", plan_id=self.plan_record["id"], decisions=entries)

    def test_clean_essential_gate_opens_optional(self):
        q = results.queue(self.plan_record, self.essential, None, 2)
        self.assertEqual(q["gate"], "clean")
        self.assertEqual(len(q["launch"]), 1)

    def test_pending_essential_gate_keeps_optional_queued(self):
        q = results.queue(self.plan_record, [], None, 2)
        self.assertEqual(q["gate"], "pending")
        self.assertEqual(len(q["launch"]), 2)
        self.assertEqual(len(q["launch"]) + len(q["queued"]), len(self.plan_record["tasks"]))

    def test_active_tasks_reduce_available_concurrency(self):
        active = [self.essential[0]["task_id"]]
        q = results.queue(self.plan_record, [], None, 2, active)
        self.assertEqual(len(q["launch"]), 1)
        self.assertFalse(set(active) & set(q["launch"]))
        with self.assertRaises(ValueError):
            results.queue(self.plan_record, [], None, 1, active * 2)
        with self.assertRaises(ValueError):
            results.queue(self.plan_record, [], None, 0)

    def test_verified_defect_blocks_entire_optional_stage(self):
        t = next(t for t in self.plan_record["tasks"] if t["aspect"] == "correctness")
        r = next(r for r in self.essential if r["task_id"] == t["id"])
        r["findings"] = [candidate(t)]
        cid = t["id"] + ":f1"
        verified = self.verification([{"candidate": cid, "disposition": "verified", "evidence": ["Base comparison confirms trigger"]}])
        q = results.queue(self.plan_record, self.essential, verified, 2)
        self.assertEqual(q["gate"], "blocked")
        self.assertEqual(len(q["skipped"]), 1)
        report = results.report(self.plan_record, self.essential, verified)
        self.assertEqual(report["findings"][0]["id"], cid)
        self.assertFalse(report["improvements"])
        self.assertIn("skipped", report["optional_reason"])

    def test_security_and_resource_checks_remain_active_after_defect(self):
        t = next(t for t in self.plan_record["tasks"] if t["aspect"] == "correctness")
        r = next(r for r in self.essential if r["task_id"] == t["id"])
        r["findings"] = [candidate(t)]
        verification = self.verification([{"candidate": t["id"] + ":f1", "disposition": "verified", "evidence": ["Confirmed"]}])
        q = results.queue(self.plan_record, [r], verification, 10)
        aspects = {x["aspect"] for x in self.plan_record["tasks"] if x["id"] in q["launch"]}
        self.assertTrue({"security", "resources"} <= aspects)
        self.assertNotIn("simplification", aspects)

    def test_unverified_candidate_keeps_gate_pending(self):
        t = self.plan_record["tasks"][0]
        self.essential[0]["findings"] = [candidate(t)]
        q = results.queue(self.plan_record, self.essential, None, 3)
        self.assertEqual(q["gate"], "pending")
        self.assertFalse(q["launch"])
        report = results.report(self.plan_record, self.essential, None)
        self.assertTrue(any("Unverified" in g["reason"] for g in report["coverage_gaps"]))

    def test_rejection_requires_evidence_and_releases_gate(self):
        t = self.plan_record["tasks"][0]
        self.essential[0]["findings"] = [candidate(t)]
        d = {"candidate": t["id"] + ":f1", "disposition": "rejected", "evidence": []}
        with self.assertRaises(ValueError):
            results.queue(self.plan_record, self.essential, self.verification([d]), 2)
        d["evidence"] = ["Supported counterexample defeats claim"]
        self.assertEqual(results.queue(self.plan_record, self.essential, self.verification([d]), 2)["gate"], "clean")

    def test_interrupted_worker_missing_hunks_and_budget_overrun_block(self):
        for mutate in (lambda r: r.update(status="failed"), lambda r: r.update(inspected_hunks=[]),
                       lambda r: r.update(extra_visible_bytes=100000), lambda r: r.update(gaps=["Truncated evidence"])):
            rows = copy.deepcopy(self.essential)
            mutate(rows[0])
            self.assertEqual(results.queue(self.plan_record, rows, None, 2)["gate"], "blocked")

    def test_profile_identity_mismatch_blocks(self):
        # The plan identity is a host assertion; receipts must match it after launch.
        self.plan_record["identity"] = identity()
        self.assertEqual(results.queue(self.plan_record, self.essential, None, 2)["gate"], "blocked")

    def test_stale_and_duplicate_receipts_rejected(self):
        rows = copy.deepcopy(self.essential)
        rows[0]["input_hash"] = "old"
        with self.assertRaisesRegex(ValueError, "stale"):
            results.queue(self.plan_record, rows, None, 2)
        with self.assertRaises(ValueError):
            results.queue(self.plan_record, self.essential + self.essential[:1], None, 2)

    def test_optional_receipts_before_clean_gate_rejected(self):
        optional = next(r for r in self.all if r not in self.essential)
        with self.assertRaisesRegex(ValueError, "clean whole-PR"):
            results.report(self.plan_record, [optional], None)

    def test_missing_receipts_show_material_gaps(self):
        report = results.report(self.plan_record, [], None)
        self.assertEqual(len(report["coverage_gaps"]), len(self.essential))
        self.assertEqual(report["gate"], "pending")

    def test_optional_improvement_distinct_from_defects(self):
        t = next(t for t in self.plan_record["tasks"] if t["stage"] == "optional")
        r = next(r for r in self.all if r["task_id"] == t["id"])
        r["findings"] = [candidate(t, "improvement", 3)]
        v = self.verification([{"candidate": t["id"] + ":f1", "disposition": "verified", "evidence": ["Benefit demonstrated"]}])
        report = results.report(self.plan_record, self.all, v)
        self.assertFalse(report["findings"])
        self.assertEqual(len(report["improvements"]), 1)

    def test_merge_requires_verified_equivalent(self):
        t0, t1 = self.plan_record["tasks"][:2]
        self.essential[0]["findings"] = [candidate(t0)]
        self.essential[1]["findings"] = [candidate(t1)]
        d = [{"candidate": t0["id"] + ":f1", "disposition": "merged", "into": t1["id"] + ":f1", "evidence": ["Same mechanism"]}]
        with self.assertRaises(ValueError):
            results.report(self.plan_record, self.essential, self.verification(d))
        d.append({"candidate": t1["id"] + ":f1", "disposition": "verified", "evidence": ["Confirmed"]})
        self.assertEqual(len(results.report(self.plan_record, self.essential, self.verification(d))["findings"]), 1)
