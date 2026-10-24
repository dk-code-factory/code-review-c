# Serious resource and performance defects (essential)

Trace acquisition/release, bounded queues and allocations, cancellation, error cleanup,
retry loops, shutdown, and work per request/item/retry. Show a realistic trigger and
consequential leak, exhaustion, unbounded growth, hot-path regression, or liveness
failure compared with base. Quantify work from code where possible. This check stays
active after other findings; optional scalability advice cannot replace it.
