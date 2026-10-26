"""Fail closed to provisional limits unless exact, measured evidence applies."""
from pathlib import Path

from .records import digest, load, local_ref

PROVISIONAL = {"total_visible_bytes": 58000, "patch_bytes": 58000,
               "support_bytes": 58000, "file_count": 1000000}
IDENTITY_FIELDS = ("model", "reasoning_effort", "codex_version", "instruction_hash")


def select(directory, identity, languages, domain, aspect):
    fallback = {"id": "provisional", "limits": dict(PROVISIONAL),
                "reason": "No compatible qualifying calibration; 58,000 bytes is provisional."}
    if not directory or not identity or any(not identity.get(k) for k in IDENTITY_FIELDS):
        return fallback
    candidates = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            p = load(path, "model-profile")
            if p["status"] != "promoted" or p["identity"] != identity:
                continue
            if p["scope"] != {"languages": sorted(languages), "domain": domain, "aspect": aspect}:
                continue
            report_path = local_ref(path.parent, p["evidence"]["report"])
            report = load(report_path, "comparison")
            if digest(report) != p["evidence"]["sha256"] or not report["qualifying"]:
                continue
            winner = next(s for s in report["strategies"] if s["id"] == p["strategy"])
            if (not winner["passing"] or winner["measured_limits"] != p["limits"] or winner["layout"] != "focused"
                    or report["identity"] != identity or report["scope"] != p["scope"]):
                continue
            if (p["quality"] != {k: winner["heldout"][k] for k in ("recall", "precision")}
                    or p["cost"] != {k: winner["heldout"][k] for k in ("tokens", "latency_seconds")}):
                continue
            if any(type(p["limits"][k]) is not int or p["limits"][k] <= 0 for k in PROVISIONAL):
                continue
            candidates.append(p)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            continue
    if not candidates:
        return fallback
    # Quality is lexicographic; resource use only breaks measured quality ties.
    candidates.sort(key=lambda p: (-p["quality"]["recall"], -p["quality"]["precision"],
                                   p["cost"]["tokens"], p["cost"]["latency_seconds"], p["id"]))
    return candidates[0]
