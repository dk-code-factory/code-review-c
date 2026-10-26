"""Validate worker receipts, parent verification, scheduling, and whole-PR gates."""
from .planner import OPTIONAL
from .records import record, validate_output


def reconcile(plan, receipts, verification):
    tasks = {t["id"]: t for t in plan["tasks"]}
    results, candidates, gaps = {}, {}, list(plan["gaps"])
    for receipt in receipts:
        validate_output(receipt, "task-result")
        if receipt.get("kind") != "task-result" or receipt.get("version") != 1:
            raise ValueError("unsupported task result")
        tid = receipt["task_id"]
        if tid not in tasks or tid in results:
            raise ValueError("unknown or duplicate task receipt")
        task = tasks[tid]
        if receipt["plan_id"] != plan["id"] or receipt["input_hash"] != task["input_hash"]:
            raise ValueError("stale task receipt")
        if receipt["status"] not in {"complete", "incomplete", "failed"}:
            raise ValueError("invalid result status")
        if len(set(receipt["inspected_hunks"])) != len(receipt["inspected_hunks"]) or set(receipt["inspected_hunks"]) - set(task["hunks"]):
            raise ValueError("reviewer claims unassigned hunks")
        extra = receipt["extra_visible_bytes"]
        if type(extra) is not int or extra < 0:
            raise ValueError("invalid visible-input accounting")
        complete = (receipt["status"] == "complete"
                    and set(receipt["inspected_hunks"]) == set(task["hunks"])
                    and task["bytes"]["total"] + extra <= task["limits"]["total_visible_bytes"]
                    and not receipt["gaps"]
                    and (plan["identity"] is None or receipt["identity"] == plan["identity"]))
        if not complete:
            gaps.append({"hunks": task["hunks"], "aspects": [task["aspect"]],
                         "reason": f"{tid}: incomplete, interrupted, identity mismatch, or budget exceeded",
                         "details": receipt["gaps"]})
        for candidate in receipt["findings"]:
            cid = tid + ":" + candidate["id"]
            if cid in candidates:
                raise ValueError("duplicate candidate ID")
            if (candidate["category"] not in {"defect", "improvement"}
                    or candidate["priority"] not in {0, 1, 2, 3}
                    or not candidate["evidence"] or not candidate["claim"]
                    or not candidate["hunks"] or not set(candidate["hunks"]) <= set(task["hunks"])):
                raise ValueError("candidate must cite evidence and owned hunks")
            if task["stage"] == "optional" and candidate["category"] != "improvement":
                raise ValueError("optional reviewers return improvements; reopen essentials for defects")
            candidates[cid] = {**candidate, "id": cid, "task_id": tid, "aspect": task["aspect"]}
        results[tid] = {"receipt": receipt, "complete": complete}
    verification = verification or record("verification", plan_id=plan["id"], decisions=[])
    if verification.get("kind") != "verification" or verification.get("version") != 1 or verification["plan_id"] != plan["id"]:
        raise ValueError("stale or unsupported parent verification")
    decisions = {}
    for d in verification["decisions"]:
        if d["candidate"] not in candidates or d["candidate"] in decisions:
            raise ValueError("unknown or duplicate verification candidate")
        if d["disposition"] not in {"verified", "rejected", "merged", "unresolved"} or not d["evidence"]:
            raise ValueError("parent decisions need an explicit disposition and evidence")
        decisions[d["candidate"]] = d
    findings, unresolved = [], []
    for cid, c in candidates.items():
        d = decisions.get(cid)
        if not d or d["disposition"] == "unresolved":
            unresolved.append(cid)
        elif d["disposition"] == "verified":
            findings.append({**c, "verification": d["evidence"]})
        elif d["disposition"] == "merged":
            target = decisions.get(d.get("into"))
            if not target or target["disposition"] != "verified":
                raise ValueError("merge target must be a verified candidate")
    essential = [t for t in plan["tasks"] if t["stage"] == "essential"]
    pending = [t["id"] for t in essential if t["id"] not in results]
    essential_gaps = [g for g in gaps if any(a not in OPTIONAL for a in g["aspects"])]
    blockers = [f["id"] for f in findings if tasks[f["task_id"]]["stage"] == "essential" and f["category"] == "defect"]
    unresolved_essential = [c for c in unresolved if tasks[candidates[c]["task_id"]]["stage"] == "essential"]
    gate = "blocked" if essential_gaps or blockers else "pending" if pending or unresolved_essential else "clean"
    if gate != "clean" and any(tasks[tid]["stage"] == "optional" for tid in results):
        raise ValueError("optional receipts require a clean whole-PR essential gate")
    return {"results": results, "findings": sorted(findings, key=lambda f: (f["priority"], f["id"])),
            "gaps": gaps, "unresolved": unresolved, "gate": gate, "blockers": blockers,
            "pending_essential": pending}


def queue(plan, receipts, verification, capacity, active=()):
    if type(capacity) is not int or capacity < 1:
        raise ValueError("capacity must be a positive host concurrency limit")
    state = reconcile(plan, receipts, verification)
    ids = {t["id"] for t in plan["tasks"]}
    if len(active) != len(set(active)) or not set(active) <= ids or len(active) > capacity:
        raise ValueError("invalid active task list or concurrency exceeded")
    if set(active) & state["results"].keys():
        raise ValueError("completed tasks cannot remain active")
    done = {k for k, v in state["results"].items() if v["complete"]}
    ready, waiting, skipped = [], [], []
    for t in plan["tasks"]:
        if t["id"] in state["results"] or t["id"] in active:
            continue
        if t["stage"] == "optional" and state["gate"] == "blocked":
            skipped.append(t["id"])
        elif (t["stage"] == "essential" or state["gate"] == "clean") and set(t["prerequisites"]) <= done:
            ready.append(t["id"])
        else:
            waiting.append(t["id"])
    available = capacity - len(active)
    return record("schedule", plan_id=plan["id"], gate=state["gate"], launch=ready[:available],
                  queued=ready[available:] + waiting, active=list(active), skipped=skipped)


def report(plan, receipts, verification):
    state = reconcile(plan, receipts, verification)
    # Missing work always remains visible, including downstream tasks blocked by interruptions.
    for t in plan["tasks"]:
        if t["id"] not in state["results"] and (t["stage"] == "essential" or state["gate"] == "clean"):
            state["gaps"].append({"hunks": t["hunks"], "aspects": [t["aspect"]],
                                  "reason": t["id"] + ": no completed reviewer receipt"})
    for cid in state["unresolved"]:
        state["gaps"].append({"hunks": [], "aspects": [], "reason": "Unverified candidate " + cid})
    optional = [t["id"] for t in plan["tasks"] if t["stage"] == "optional"]
    reason = ("Whole-PR essential gate " + state["gate"] + "; optional stages skipped."
              if state["gate"] != "clean" else plan["optional_reason"] if not optional else "Optional stages warranted by controller analysis.")
    return record("review-report", plan_id=plan["id"], base=plan["base"], head=plan["head"],
                  findings=[f for f in state["findings"] if f["category"] == "defect"],
                  improvements=[f for f in state["findings"] if f["category"] == "improvement"],
                  coverage_gaps=state["gaps"], gate=state["gate"], optional_reason=reason)
