# Model onboarding

Follow [OpenAI's skill evaluation guidance](https://developers.openai.com/blog/eval-skills):
define observable success, capture execution traces, and grade behavior with repeatable
checks. The runner uses the current [non-interactive CLI interface](https://developers.openai.com/codex/noninteractive)
for `--json`, `--output-schema`, `--ephemeral`, and read-only execution. It does not
use the evaluation article's older `--full-auto` example. These procedures and the
harness are infrastructure; no shipped profile claims measured capacity.

`HELPER` means `SKILL_DIR/scripts/review.py`, invoked with `python3`. Keep campaigns,
gold keys, traces, receipts, historical imports, and profiles in private external
storage. Preparation, freezing, grading, comparison, and promotion launch no models.

## Prepare fixtures and freeze

1. Run `HELPER onboard prepare --out PRIVATE_PREPARATION`. This writes an empty
   campaign spec and a matrix of required dimensions. It makes no model calls.
2. Author realistic fixture repositories with committed base/head revisions and
   gold defects, plus clean negative cases. Keep answer keys **outside** fixture
   trees and Git history. Verify both trees and comments contain no answers or
   labels. Freeze exports only regular files/directories, rejecting symlinks and
   submodules. Curate fixtures rather than claiming generated filler proves quality.
3. Fill `spec.json` using [the contracts](contracts.md). Resolve exact host-selected
   model/effort and the runner's `codex --version`, and use `identity-hash` for the
   installed skill. Record the tested language set, domain and primary aspect.
   Do not substitute a model or infer reviewer identity from the controller.
4. Set explicit campaign limits: maximum model calls, total elapsed trial seconds,
   and per-trial seconds. Zero defaults prohibit execution. All started calls,
   including failed and interrupted attempts, consume the call allowance. Elapsed
   time of finished attempts and reserved time for interrupted attempts consume
   the frozen time allowance. These are call/time limits, not a guaranteed dollar
   or token cap; no token price assumptions are made.
5. Freeze with `HELPER onboard freeze --spec SPEC --out CAMPAIGN`. Frozen source
   trees, prompt hashes, identity, limits and gold keys are immutable. If a fixture,
   prompt, instruction or gold key changes, create another campaign.

Measure below, around, and above 58,000 bytes; vary supporting-context volume,
file count, and early/middle/late defects, with clean controls. Use realistic
supporting code and multiple independent defects. For multi-language scope include
single-language, demonstrated related-language interfaces, and unrelated languages.
For a single-language profile only the single-language layout is required; separate
multi-language campaigns evaluate interface grouping and language interference.
Compare focused versus combined aspects as matched controls; this release promotes
only focused layouts because production tasks have a single primary purpose.

Do not treat author labels as measurement: annotate the raw patch/support bytes and
file counts, inspect actual rendered sizes in the frozen campaign, and audit the
declared matrix. Patch-size bands are <56,000, 56,000–60,000, and >60,000 raw displayed
patch bytes. Low/high support is <8,000 / >=8,000 bytes; few/many files is <=4 / >=10.
Five to nine files do not satisfy either boundary control. Related-language fixtures
must cite a real API/schema/transport relationship in supplied evidence; unrelated
ones must have independent behavior. The harness validates measurable dimensions;
semantic relationships and gold correctness require human audit.

Use separate training and held-out fixture trees. Duplicate trees cannot appear
in both splits. Require all dimensions across the campaign, and gold defects and
clean cases in each split. Use three matched repetitions for every fixture and
strategy. Do not tune against held-out results; changing strategy after inspecting
them requires new held-out fixtures. Strategies may vary task capacity and focus;
keep all other evidence identical within each matched trial.

## Run isolated trials

Provide an immutable Docker image (`IMAGE@sha256:...`) containing the desired Codex
version and its runtime dependencies, with `codex` on PATH and no custom entrypoint.
Provide a sanitized host config containing **only** `model` and
`model_reasoning_effort`, matching the frozen host-selected identity. The harness
does not invent candidate overrides. Provide the existing host's `auth.json`; it
is copied into an ephemeral private home and removed on exit. API-key or custom
provider setups need an audited equivalent image/auth adapter; this runner supports
normal Codex auth.json login. Do not mount the host's full Codex home.

After explicitly authorizing model calls with limits, run:

```sh
python3 HELPER onboard run --campaign CAMPAIGN --image IMAGE@sha256:DIGEST \
  --host-config SANITIZED_CONFIG --auth AUTH_JSON \
  --max-calls 12 --max-wall-seconds 1800 --trial-seconds 180
```

The container sees only pinned base/head trees, its exact prompt/schema, an output
directory, and temporary authentication/config. Gold keys, grader decisions, campaign
manifests, earlier outputs, and original repository paths are never mounted. The
container's network permits Codex authentication/inference; the prompt prohibits
reviewer browsing. Docker isolation hides keys independently of prompt compliance.
Do not run untrusted image entrypoints or use an image that ignores its command.

Each call is an isolated `codex exec --ephemeral --json --sandbox read-only` session
with a structured calibration output schema. No child agents are permitted. The
runner captures traces, stderr, structured findings, timing, usage, and input hashes.
It executes serially to make matched latency easier to assess. Explicit limits stop
new launches, timeouts remove containers, and existing attempts are never overwritten.
An interrupted `started` receipt remains incomplete; preserve it. No automatic retry
turns a failed attempt into a passing one. Start a fresh campaign for replacement trials.

## Grade, compare, promote

Grade every finding outside the reviewer container. Write `grading-decisions` with
one disposition per finding: matched to a unique gold defect, false-positive, or
unresolved, each with evidence. Adjudicate alternate valid findings before deciding
they are false positives; a genuinely incomplete key requires a new frozen campaign.
Run `HELPER onboard grade --campaign CAMPAIGN --attempt FIXTURE--STRATEGY--0
--decisions DECISIONS`. Even failed attempts can retain grades; missing outputs
are preserved and cannot qualify. Keep false positives and unresolved candidates.

Run `HELPER onboard compare --campaign CAMPAIGN`. Promotion requires three complete,
graded, matched repetitions on every fixture, a separate held-out split, complete
matrix controls, no missed critical gold defect, and no completion/measurement gaps.
Mean recall and precision must meet or exceed the focused baseline in **both**
training and held-out data. Rank passing strategies by held-out quality, then training
quality, then measured token use and latency. Missing usage is missing evidence,
never a free run. Failed attempts and unresolved findings cannot disappear from a
comparison. The false-positive rate on clean fixtures affects precision.

Use `HELPER onboard promote --campaign CAMPAIGN --strategy STRATEGY --out PRIVATE_PROFILES`.
Only the highest-ranked passing focused strategy can be promoted. Exported limits
are capped by the largest successfully measured total input, patch, support and
file count, never the untested configured maximum. These are measured marginal
limits, not a claim that every combination of maxima was tested. The exact identity,
instruction hash, scope, report hash and per-trial evidence hashes remain attached.
Human audit of gold keys/semantic dimensions and host identity is still required;
no automated score proves general review quality. Recalibrate after instruction,
runtime or model changes. Do not add private profiles to the public plugin.

## Reuse prior evidence

`HELPER onboard import --campaign CAMPAIGN --source OLD_CAMPAIGN --format native`
imports attempts without model calls only when the entire frozen campaign identity
matches; comparison revalidates all receipts, traces, grades and hashes. Conflicts
are errors, never replacements. Other native campaigns are retained as historical.

For optional `skill-bench` integration, export its evidence as a JSON manifest and
use `--format skill-bench`. The adapter preserves the manifest and provenance as
historical evidence; it has no dependency on skill-bench. Existing C++, Go and Rust
trials can supply fixture leads and historical comparisons. They do not establish
capacity for this skill because its instructions and workflow differ. To reuse
compatible measurements as capacity evidence, export the complete native frozen
campaign contract, including traces and independently graded receipts; a summary
or model name alone cannot qualify.
