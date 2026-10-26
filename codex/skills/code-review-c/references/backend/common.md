# Backend evidence lenses

Use only lenses relevant to the task's primary purpose. Trace API input/output and
compatibility; authentication versus object/tenant authorization; persistence and
transaction boundaries; schema/migration compatibility during mixed-version deploys;
async ownership, retries, cancellation and shutdown; and resource cleanup after every
exit. For retries inspect stored state used by the next attempt, idempotency, backoff
and whether progress needs external change. For pooled resources inspect expiry and
reconfiguration. Evidence of an actual contract violation is required; retained desired
state or a delayed retry can be intentional.
