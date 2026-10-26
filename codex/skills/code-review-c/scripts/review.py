#!/usr/bin/env python3
"""Adaptive review mechanics; model work stays in native Codex agents."""
import argparse
import json
import sys
from pathlib import Path

from reviewlib import inventory, onboarding, planner, results
from reviewlib.records import external, instruction_hash, load, save


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="command", required=True)
    q = subs.add_parser("identity-hash", help="print current skill/instruction hash; does not resolve model identity")
    q = subs.add_parser("inventory")
    q.add_argument("--repo", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--base")
    q.add_argument("--head")
    q.add_argument("--pr", help="PR number or GitHub URL; uses gh and pins metadata")
    q = subs.add_parser("plan", help="preview a strategy; launches no reviewers")
    q.add_argument("--bundle", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--analysis", help="controller semantic grouping and focused aspects JSON")
    q.add_argument("--identity", help="resolved REVIEWER identity JSON, not controller identity")
    q.add_argument("--profiles")
    q.add_argument("--host-input-bytes", type=int, default=8000)
    for command in ("validate", "prompt", "expand", "next", "report"):
        q = subs.add_parser(command)
        q.add_argument("--bundle", required=True)
        q.add_argument("--plan", required=True)
        if command in {"prompt", "expand"}:
            q.add_argument("--task", required=True)
            q.add_argument("--out", required=True)
        if command == "expand":
            q.add_argument("--evidence", required=True)
        if command in {"next", "report"}:
            q.add_argument("--results", required=True, help="directory of task-result JSON records")
            q.add_argument("--verification")
        if command == "next":
            q.add_argument("--capacity", type=int, required=True)
            q.add_argument("--active", nargs="*", default=[])
    q = subs.add_parser("onboard")
    modes = q.add_subparsers(dest="mode", required=True)
    r = modes.add_parser("prepare")
    r.add_argument("--out", required=True)
    r = modes.add_parser("freeze")
    r.add_argument("--spec", required=True)
    r.add_argument("--out", required=True)
    for mode in ("run", "grade", "compare", "promote", "import"):
        r = modes.add_parser(mode)
        r.add_argument("--campaign", required=True)
        if mode == "run":
            for name in ("image", "host-config", "auth"):
                r.add_argument("--" + name, required=True)
            for name in ("max-calls", "max-wall-seconds", "trial-seconds"):
                r.add_argument("--" + name, required=True, type=int)
        if mode == "grade":
            r.add_argument("--attempt", required=True)
            r.add_argument("--decisions", required=True)
        if mode == "promote":
            r.add_argument("--strategy", required=True)
            r.add_argument("--out", required=True)
        if mode == "import":
            r.add_argument("--source", required=True)
            r.add_argument("--format", choices=("native", "skill-bench"), default="native")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    if a.command == "identity-hash":
        return {"instruction_hash": instruction_hash()}
    if a.command == "inventory":
        return inventory.summary(inventory.build(a.repo, a.out, a.base, a.head, a.pr))
    if a.command == "plan":
        inv = inventory.read(a.bundle)
        output = external(a.out, inv["repo"])
        plan = planner.build(a.bundle, load(a.analysis) if a.analysis else None,
                             load(a.identity) if a.identity else None, a.profiles, a.host_input_bytes)
        save(output, plan)
        return plan
    if a.command == "onboard":
        if a.mode == "prepare":
            return onboarding.prepare(a.out)
        if a.mode == "freeze":
            return onboarding.freeze(a.spec, a.out)
        if a.mode == "run":
            return onboarding.run(a.campaign, a.image, a.host_config, a.auth, a.max_calls, a.max_wall_seconds, a.trial_seconds)
        if a.mode == "grade":
            return onboarding.grade(a.campaign, a.attempt, a.decisions)
        if a.mode == "compare":
            return onboarding.compare(a.campaign)
        if a.mode == "promote":
            return onboarding.promote(a.campaign, a.strategy, a.out)
        return onboarding.import_evidence(a.campaign, a.source, a.format)
    inv, plan = inventory.read(a.bundle), load(a.plan, "review-plan")
    planner.validate(plan, inv, a.bundle)
    if a.command == "validate":
        return {"valid": True, "plan_id": plan["id"]}
    if a.command in {"prompt", "expand"}:
        out = external(a.out, inv["repo"])
        if a.command == "expand":
            plan = planner.expand(plan, a.bundle, a.task, load(a.evidence))
            save(out, plan)
            return plan
        task = next(t for t in plan["tasks"] if t["id"] == a.task)
        prompt, accounting = planner.render(task, inv, a.bundle)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt)
        return {"prompt": str(out), "bytes": accounting, "input_hash": task["input_hash"]}
    receipts = [load(p, "task-result") for p in sorted(Path(a.results).glob("*.json"))]
    verification = load(a.verification, "verification") if a.verification else None
    if a.command == "next":
        return results.queue(plan, receipts, verification, a.capacity, a.active)
    return results.report(plan, receipts, verification)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, sort_keys=True, ensure_ascii=True))
    except (ValueError, KeyError, OSError, StopIteration, __import__("subprocess").SubprocessError) as error:
        print(json.dumps({"error": str(error), "status": "incomplete"}), file=sys.stderr)
        sys.exit(2)
