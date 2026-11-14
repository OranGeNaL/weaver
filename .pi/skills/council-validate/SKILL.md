---
name: council-validate
description: Validate an OpenSpec change, branch, or diff with a two-pass read-only advisor council and return a severity-ranked memo. Use when the user asks to validate/провалидировать a change or implementation with subagents/advisors, to cross-examine a "done" claim, to audit spec-vs-code compliance, or to decide whether a change is safe to archive.
---

# Council validation

Two independent read-only advisors review the same brief, then one curated cross-exam settles what they dispute. The parent writes the memo and decides. Advisors never edit, never see each other's reports, and never run the fix.

Run this before archiving a change or after reporting it "complete": the point is to catch the defects that a green test run hides. Validation output is not task completion — it can revoke an earlier completion claim.

This project wires the skill in: `openspec/config.yaml` (`operations.apply.guidance`, `operations.archive.guidance`, `rules.tasks`) requires it after implementation and before archive, and the `openspec-apply-change` / `openspec-archive-change` skills call it. A change is not done and not archivable without its memo.

## Steps

### 1. Frame the question

Write down: the target (change name, branch, or diff range), the delta specs in scope, the code and tests in scope, explicit non-goals (later stages, style), and the blocking bar.

Blocking bar, applied in this order:

- **blocker** — violates a delta-spec scenario, a security contract, or risks data loss.
- **P1** — wrong state or misleading output the user acts on.
- **P2** — cleanup, dead code, wording, missing test for a non-blocking branch.

Completion criterion: every claim later lands in exactly one of these tiers, or in owner decisions.

### 2. Pick the roster

```bash
subagent({ action: "list" })
```

Prefer 2–3 executable `council-*` profiles. If there are fewer than two, fill with `oracle` (pass `context: "fork"`) then `reviewer` (its normal profile context). Never exceed four. Announce the roster, each advisor's context mode, and the pass cap (default 2; a third pass needs an explicit `--max-passes 3`).

Completion criterion: ≥2 advisors named, context modes stated, no invented per-run role labels.

### 3. Collect parent evidence

Run the repository's own checks and record the exact commands and results:

```bash
cd hub && ruff check . && ruff format --check .
TEXT_DATABASE_URL=... pytest -q
cd web && npm run typecheck && npm run build
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

Add anything the change claims but tests do not cover (manual browser run, migration on a non-empty database). Hand this to the advisors as "parent-verified evidence — inspect the code to challenge it, do not re-run".

Completion criterion: the evidence block contains only commands actually run, each with its result or an explicit "not run".

### 4. Write the brief

The brief is the whole contract. Assemble it as `args.brief`:

```text
COUNCIL PASS 1 (independent, read-only). Repo: <abs path>, <branch/wt state>.
QUESTION: <is X correctly and completely implemented against <specs>, and which defects MUST be fixed before <archive/merge>?>
SCOPE: <delta specs, modules, migration, tests, web files, docs — paths>
NON-GOALS: <later stages, style, unrelated debt>
PARENT-VERIFIED EVIDENCE: <commands and results>
CHECK AT LEAST THESE CONTRACT POINTS: <numbered spec scenarios and invariants, each with the file to verify>
RULES: inspect evidence directly; do not read peer reports; read-only; no children; no edits; cite file:line; ≤600 words; structured report only.
```

The numbered points are the work. Enumerate every requirement and scenario of the delta specs, the security invariants (rotation/replay, cookies, nonce/state, rate limits, secret handling, gating), and every adversarial hunch you have (races, query predicates, fail-open error handlers, migration reversibility). Mark your hunches as hunches so advisors test them instead of trusting them.

Completion criterion: each delta-spec requirement and each invariant appears at least once in the numbered list.

### 5. Launch pass 1 (async workflow)

Use the skill's script with your brief and roster:

```text
subagent({
  workflow: "<this skill's directory>/assets/pass1.js",
  args: { brief: "<brief>", roster: [ {key, agent, context?, label, role}, ... ] },
  async: true,
  cwd: "<repo>"
})
```

Return one receipt: launched count, run id. Do not paste advisor transcripts; on completion, read `status.json` (`workflow.value.advisors[]`) and build the claim matrix in the parent.

Completion criterion: every advisor row has `report` with `recommendation`, `evidence[].sources`, `risks`, `confidence`.

### 6. Verify before you believe

For each claim, open the cited lines. Drop or downgrade claims whose citation does not say what the advisor claims — this filter is the parent's job, not the board's.

Then group into: **agreement (k/n)**, **disputed**, **missing proof**, **out of scope**.

Completion criterion: every claim is grouped, and every disputed claim names the exact sentence of spec or code that could settle it.

### 7. Launch pass 2 (one cross-exam)

Only for disputed claims and claims without a counter-argument. Give each advisor the same packet: claim id, the claim, the strongest conflicting evidence with quotes, and what decision is needed. Attribute peer content as "another advisor"; never include full peer reports.

```text
subagent({
  workflow: "<this skill's directory>/assets/pass2.js",
  args: {
    packet: "<curated packet>",
    targets: [ {key: "cross-a", runId: "<pass1 run id>", label: "..."}, ... ]
  },
  async: true,
  cwd: "<repo>"
})
```

A resume needs the retained run id and the task; a non-resumable advisor gets a fresh run with its pass-1 report and the packet, labelled "fresh-context fallback".

Completion criterion: each relayed claim has a disposition (`accept` / `reject` / `refine` / `owner-decision`) with a source, and each advisor's `recommendationChanged` is recorded.

### 8. Write the memo

Stop at convergence, the pass cap, or an unresolved owner decision. No third pass for polish.

```markdown
# Council memo
**Question / scope**
**Verdict:** BLOCK | FIX-FIRST (list) | PASS
**Consensus table:** id | defect | who found it | severity | evidence (file:line)
**Rejected / downgraded after cross-exam:** claim, reason
**Owner decisions:** numbered, each with the recommendation
**Verification evidence + run ids**
**Confidence and what would change the decision**
**Roster, passes, fallbacks, context modes**
```

Completion criterion: no defect without a tier, no dispute without a disposition or an owner decision, and the memo names the exact fix per blocker.

## Fixing (only after the memo)

Validation ends with the memo. Apply fixes only on explicit authorization; when authorized, fix blockers first, add the regression test the advisors named, then re-run step 3 and re-run pass 1 for the fixes.

## Guardrails

- Advisors stay read-only: no edits, no children, no peer contact; the parent edits.
- Keep the parent's own code frozen while advisors read — a moving tree invalidates their citations.
- Verify citations before accepting a claim; an advisor that cites wrong lines is a red flag for its whole report.
- Keep the pass cap. Fewer, better-argued claims beat more rounds.
- If a child fails, report the failure and stop; do not silently switch execution modes.
- Treat validation output as evidence about the change, not as planning text: do not copy transcripts into `tasks.md`, and do not mark tasks done from it.
- When validation finds a blocker, say plainly that an earlier "all done" report was premature.
