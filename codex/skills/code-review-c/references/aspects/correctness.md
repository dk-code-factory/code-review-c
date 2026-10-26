# Correctness

Trace changed control flow, state transitions, boundary values, and supported failure
paths against base. Check early returns that bypass updates, ordering of completion,
stale state after failure, and assumptions made by callers. Read changed comments for
factual contradictions with those paths. Report supported defects, not alternative designs.
