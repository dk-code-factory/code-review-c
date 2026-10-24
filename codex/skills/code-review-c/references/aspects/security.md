# Security

Follow changed trust boundaries: identity, authorization, tenant/object ownership,
untrusted input, filesystem/process/query sinks, credentials and logs. Verify that
validation occurs on every reachable supported path, including retries and alternate
APIs. Establish attacker-controlled input, a reachable sink, and impact. Keep this
check active even when correctness reviewers have findings; omit generic hardening.
