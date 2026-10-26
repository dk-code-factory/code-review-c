---
name: code-review-c
description: Review a pull request or pinned local revisions with adaptive focused Codex agents, preview a review strategy, or onboard a model using isolated evaluation campaigns.
---

Use this session as the controller. Resolve bundled paths from this skill directory,
not the reviewed repository. Python 3.11+, Git, and native Codex agents are required;
PR pinning also uses authenticated `gh`. No other skill or plugin is required.

Choose the requested mode:

- Review or preview: read [the controller procedure](references/controller.md).
  Preview stops after validated planning and reports the proposed strategy and gaps.
- Onboard a model or import prior measurements: read [the onboarding procedure](references/onboarding.md).
  Preparation and comparison run no models. Paid trials require explicit limits.

In review mode, use fresh native agents without inherited conversation for each
task. Reviewers do not spawn agents. Inherit the host-selected model and reasoning
effort; never set reviewer overrides. Resolve the actual reviewer identity from host
metadata when available. The controller's identity does not establish it. Unknown
identity, changed instructions, or missing compatible measurements require the
provisional strategy: at most **58,000 bytes total visible input per task**, including
patch, instructions, evidence, framing, and host-visible instructions. This is an
initial safety budget, not an established capacity or a token count.

Cover every changed hunk and all essential aspects before optional improvements.
Keep security and serious resource/performance checks active when other reviewers
find defects. Verify essential candidates before opening later stages. A verified
essential defect, unresolved essential finding, or incomplete essential coverage
blocks optional reviews across the entire PR. Queue remaining work within available
host concurrency; never reduce coverage to fit the number of agents.

Use measured profiles only for their exact identity, instruction hash, language,
domain, and primary review aspect. Prefer quality; use tokens and latency to break
quality ties. Keep all campaign data, traces, local profiles, and review artifacts
outside this plugin and the reviewed repository.

Return verified defects in priority order, then any separately labeled optional
improvements, material coverage gaps, and the reason optional stages were skipped.
Keep reports concise. Review does not authorize code edits or GitHub publication.
