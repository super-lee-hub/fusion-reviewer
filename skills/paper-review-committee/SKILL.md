---
name: paper-review-committee
description: Committee-style paper review for PDF, DOCX, and DOC papers with 8 subagents, shared evidence normalization, editor synthesis, and artifact bundles. Triggered by: paper review, PDF, DOCX, DOC, committee review, journal fit, revision responses, evidence bundles, Codex, Claude Code.
---

# Paper Review Committee

## Overview

This skill implements committee-style academic paper review. The host agent (Codex or Claude Code) handles reasoning, reviewer task orchestration, editor synthesis, and revision judgments. The deterministic core library (`fusion_reviewer`) handles document normalization, evidence preparation, schema validation, quote verification, concern merging, and artifact rendering.

Standard workflow:

1. Normalize the paper into a shared evidence bundle via `prepare_paper.py`.
2. Classify the research paradigm, writing `evidence/manuscript_classification.json`.
3. Launch 8 reviewer subagents in parallel: 3 generalist + 5 specialist.
4. The host agent acts as editor: deduplication, consensus vs. disagreement, synthesis, and artifact writing.
5. Finalize outputs via `finalize_run.py`.

## Included Files

- `SKILL.md` — Main workflow, boundaries, commands, and review rules
- `agents/openai.yaml` — Codex UI/plugin display and default prompt
- `agents/claude.yaml` — Optional Claude Code project metadata
- `references/roles.yaml` — 8 reviewers, editor, classification gate, and evidence rules
- `references/artifact_contract.md` — Output directory structure, JSON fields, and merge rules
- `references/paradigm_criteria.yaml` — Research paradigm classification labels and method review criteria
- `references/schemas/` — 8 JSON Schema files for all review artifacts
- `scripts/prepare_paper.py` — Generate shared evidence bundle
- `scripts/finalize_run.py` — Finalize review artifacts from reviewer/editor JSON
- `scripts/install_skill.py` — Install skill to Codex/Claude skills directory

Do not include `__pycache__/`, `.pyc`, temporary output directories, personal run results, `.env` files, papers, or API keys when packaging or sharing.

## Prerequisites

The `fusion_reviewer` core package must be importable. Install it from the repo root:

```bash
pip install -e .
```

## Revision Handling

When the directory or filename contains any of the following markers, this is a revision review:
`返修`, `修订`, `revised`, `revision`, `response`, `修改说明`, `答复`

Revision reviews follow a dual-track approach:

1. **Check for revision notes**: Verify `evidence/revision_notes.md` exists.
2. **If missing**: Ask the user for revision notes/response letter. Do not pretend revision response was reviewed.
3. **If garbled or too short**: Note in the final report that revision response is unreadable/incomplete.
4. **If normal**: Reviewers evaluate BOTH the revised manuscript quality AND the quality of responses to previous concerns.
5. **Revision assessments**: Host agent generates `previous_concerns.json`, `revision_claims.json`, and reviewer-level `revision_assessment[]`. Editor-level `revision_response_review.json` is optional.
6. **Downgrade rule**: `revision_assessment.status=addressed` MUST have at least one valid revised-manuscript evidence ref, otherwise downgrade to `partially_addressed` or `unclear`.
7. **New concerns**: Use `findings[].origin = "new_after_revision"` — do NOT use a standalone `new_concerns[]` field.
8. **Disagreement exposure**: If editor-level revision synthesis conflicts with reviewer assessments, the final report MUST explicitly expose the disagreement.

## Provenance Taxonomy

Every reviewer output must declare its provenance:

- `review_source`: `subagent` | `serial_local` | `local` | `unknown`
- `agent_host`: `codex` | `claude_code` | `other` | `unknown`

Legacy `service` values map to `unknown`. Old `provider_profile`/`model` fields are retained as metadata only.

## Committee Modes

| Reviewer Count | Mode | Behavior |
|---------------|------|----------|
| 0 | N/A | Fail — cannot proceed |
| 1–2 | `draft_only` | Do not claim consensus |
| 3–7 | `partial` | Limited confidence |
| 8 | `full` | Full committee |

In draft/partial mode, revision conclusions must also be marked draft/partial/low-confidence unless a separate sufficient host-produced revision synthesis exists.

## Applicable Scenarios

- Input is a PDF, DOCX, or DOC paper
- User wants committee-style peer review
- User wants multiple independent reviewer perspectives
- User wants artifacts written, not just chat summaries
- User wants journal-style review standards applied

## Workflow

### 1. Shared Preprocessing

Always normalize the source file first:

- PDF: Prefer MinerU; fall back to local structured extraction; plain text as last resort
- DOCX: Prefer Word → PDF → MinerU/PDF pipeline
- DOC: Must use LibreOffice to convert to PDF, otherwise fail clearly

All reviewers share the same evidence bundle. Never have each reviewer re-parse the source independently.

### 2. Research Paradigm Classification

Before reviewers start, classify the research paradigm from the shared evidence:

- Output: `evidence/manuscript_classification.json`
- Reference: `references/paradigm_criteria.yaml`
- Classification: Determine `coarse_family` first, then list one or more `paradigm_labels`
- Each label must include `confidence` and `evidence_refs`
- Mixed/uncertain papers may have multiple labels — do not force single-label classification

Classification determines which method review criteria to use. Never write `N/A` for method review on non-empirical papers:

- **Theoretical/formal**: Review assumptions, derivations, proofs/model logic, boundary conditions
- **Game theory/mathematical**: Review strategy spaces, payoff structures, equilibrium concepts, derivation rigor
- **Review/meta-analysis**: Review search strategy, inclusion/exclusion, evidence synthesis, quality assessment
- **Empirical**: Review identification strategy, data, measurement, robustness, inference boundaries

If classification confidence is low, document the uncertainty and instruct reviewers to apply criteria appropriate to the paper's apparent methodology.

### 3. Parallel Reviewer Launch

Fixed roles: 3 generalist + 5 specialist reviewers.

Each reviewer MUST:
- Work only from the shared evidence bundle
- Return structured JSON
- Not write the final report directly
- Explicitly declare `review_source` (must be `subagent` or `local`)
- Declare `agent_host` (`codex` or `claude_code`)
- Reference `manuscript_classification.json` and apply paradigm-appropriate method criteria

In Codex/Claude Code mode, 8 reviewer subagents default to the current frontier or standard model configuration. Do not hardcode outdated model names. Use higher reasoning effort for reviewers.

If platform concurrency limits prevent launching all 8 at once (e.g., only 6 slots available), launch available slots first and fill remaining slots as reviewers complete. Do not abort the review due to concurrency limits.

### 4. Host Agent as Editor

The host agent handles:
- Merging duplicate concerns
- Distinguishing consensus from disagreement
- Filtering critical issues without evidence support
- Downgrading or removing paradigm-mismatched concerns (e.g., demanding empirical identification for a pure theory model)
- Incorporating journal requirements into final judgment
- Outputting final report / meta review / concerns table

### 5. Artifact Writing

All outputs go to the configured output root (default: `review_outputs/`):

`review_outputs/<run_label>/`

A human-friendly entry point is also generated:

`review_outputs/按论文查看/<paper_name>/最新结果/`

Prefer the `按论文查看` directory for human access — no need to remember long run directory names.

## Journal Requirements Input

If the user provides journal style/review standards/section requirements:

- Preprocessing writes the text or file to `evidence/journal_requirements.md`
- All 8 reviewers and the editor reference this content
- The final report distinguishes:
  - Issues with the paper itself
  - Issues of fit with the target journal

## Result Directory Navigation

Each run produces two directory trees:

1. Programmatic run directory: `review_outputs/<run_label>/`
   - Core files: `final_report.md`, `meta_review.md`, `concerns_table.csv`, `final_summary.json`, `reviews/`

2. Human-friendly directory: `review_outputs/按论文查看/<paper_name>/最新结果/`
   - `00-结果说明.txt`
   - `01-审稿总报告.md` (→ `final_report.md`)
   - `02-审稿总报告.pdf` (→ `final_report.pdf`)
   - `03-元审稿.md` (→ `meta_review.md`)
   - `04-问题汇总.csv` (→ `concerns_table.csv`)
   - `05-运行摘要.json` (→ `final_summary.json`)
   - `07-提取诊断.json` (→ `evidence/diagnostics.json`)
   - `08-期刊要求.md` (→ `evidence/journal_requirements.md`)
   - `10-Reviewer逐份意见/` (→ `reviews/`)

Revision reviews additionally contain `06-返修回应审稿.md`.

`10-Reviewer逐份意见/` contains fixed-name individual reviewer reports:
- `11-委员会审稿-A.md` through `25-结构专家审稿.md`

## Operational Rules

- Reviewers are independent — they do not read each other's outputs
- Reviewers share evidence but not conclusions
- Both reviewers and editor MUST reference `evidence/manuscript_classification.json`
- Method review must be paradigm-adapted — never write `N/A` for method review on theoretical/formal/review papers
- If a reviewer fails, do not abort the entire run
- The final report MUST explicitly mark missing reviewer slots
- If only 6 true subagent reviewers + 2 local reviewers completed, the report must state `6 subagent + 2 local`, not `8 subagent reviewers`
- If document extraction fidelity was degraded, warn the user in the final report
- For Word documents that cannot be reliably converted to PDF, clearly state the fidelity risk

## Failure and Capability Policy

- **Required artifacts**: Markdown final report, JSON summary, concerns table
- **Optional/best-effort**: PDF report, MinerU, LibreOffice, ReportLab, deepreview
- **DOCX**: Degrades to text-only extraction with fidelity diagnostics when LibreOffice is missing
- **Legacy DOC**: Fails clearly without a reliable converter — do not produce misleading evidence
- **Quote verifier**: Supports `exact`, `normalized`, `page_line_only`, `not_found` match types; normalized matching handles whitespace, newlines, hyphenated word breaks, and common Chinese/English punctuation differences
- **Privacy**: Real papers, review outputs, response letters, journal requirement files, and API keys must NOT be committed or packaged

## Recommended Commands

Prepare shared evidence:

```powershell
python skills/paper-review-committee/scripts/prepare_paper.py --paper <path> [--journal-text <text>] [--journal-file <path>] [--revision-file <path>] [--previous-review-file <path>] [--previous-review-dir <path>] [--previous-concerns-file <path>] [--revision-claims-file <path>] [--original-paper <path>] [--output-root <dir>] [--run-id <id>] [--force]
```

With journal requirements:

```powershell
python skills/paper-review-committee/scripts/prepare_paper.py --paper .\paper.pdf --journal-file .\journal_requirements.txt
```

For revision reviews:

```powershell
python skills/paper-review-committee/scripts/prepare_paper.py --paper .\revised_paper.docx --revision-file .\response_letter.docx --previous-review-dir .\previous_review_outputs
```

Custom output root:

```powershell
python skills/paper-review-committee/scripts/prepare_paper.py --paper .\paper.pdf --output-root D:\somewhere\review_outputs
```

Without `--output-root`, defaults to the configured `DATA_DIR`.

Finalize from reviewer JSONs:

```powershell
python skills/paper-review-committee/scripts/finalize_run.py --run-dir .\review_outputs\<run_label> [--reviews-dir <dir>] [--editor-file <path>] [--revision-response-file <path>] [--strict]
```

With editor synthesis:

```powershell
python skills/paper-review-committee/scripts/finalize_run.py --run-dir .\review_outputs\<run_label> --editor-file .\review_outputs\<run_label>\editor_input.json
```

Once `reviews/` exists, always use the formal finalize entry point. Do not create temporary `tmp_finalize_*.py` scripts to assemble reviewer results.

## Local Helper Scripts

- `scripts/prepare_paper.py` — Prepare shared evidence bundle with environment status output
- `scripts/finalize_run.py` — Finalize from `reviews/` directory, auto-rebuild review inputs, generate all artifacts
- `scripts/install_skill.py` — Sync skill source from repo to Codex/Claude skills directory
- `references/paradigm_criteria.yaml` — Research paradigm classification criteria

## Reference Files

- `references/roles.yaml` — Role definitions, focus areas, editor responsibilities
- `references/artifact_contract.md` — Artifact directory structure and required files
- `references/paradigm_criteria.yaml` — Classification labels and paradigm-specific review criteria
- `references/schemas/` — JSON Schema contracts for all review artifacts
