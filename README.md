# paper-review-committee / fusion-reviewer

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/super-lee-hub/fusion-reviewer)

Platform-neutral Agent Skill for committee-style academic paper review — the host agent does reasoning, reviewer/editor/revision judgment; the deterministic core library does evidence preparation, schema validation, quote verification, concern merge, and artifact rendering.

## What it is

- A **platform-neutral Agent Skill** (`skills/paper-review-committee/`) that instructs a host agent (Codex, Claude Code, or any agent runtime with subagent/task support) how to run a full committee review: classify the manuscript paradigm, dispatch 8 reviewer tasks/subagents, write reviewer JSON under `reviews/`, optionally produce an editor synthesis, and produce a revision-response review.
- A **deterministic Python core library** (`src/fusion_reviewer/`) that handles all operations that must be correct rather than creative: PDF/DOCX/DOC preprocessing, MinerU integration, evidence normalisation, schema validation of reviewer/editor JSON, quote-attribution verification against manuscript evidence, concern deduplication and merge, and final-report rendering (Markdown, concerns table, optional PDF appendix).
- A **revision-review workflow** that compares previous concerns against a response letter and the revised manuscript, producing per-concern assessments (`addressed` requires valid manuscript evidence).

## What it is not

- Not a Web UI.
- Not a backend provider gateway.
- No `fusion-review submit/status/result/serve` CLI.
- No provider profile routing.
- No API keys for OpenAI / Anthropic / Gemini stored or managed inside this repo.
- No local mock-review loop — the model calls happen inside the host agent, not inside the core library.

## Repository layout

```
skills/paper-review-committee/   # Agent Skill (SKILL.md, references/, agents/, scripts/)
src/fusion_reviewer/             # Deterministic core library
tests/                           # pytest suite
scripts/                         # Schema/code-generation helpers
review_outputs/                  # Local run artifacts (git-ignored)
review_inputs/                   # Local paper staging (git-ignored)
```

## Setup

Requires Python ≥ 3.13.

```bash
pip install -e .
```

### Optional extras

```bash
pip install -e ".[pdf]"    # PDF export (reportlab)
pip install -e ".[dev]"    # pytest, jsonschema
```

### Environment variables

Copy `.env.example` to `.env` and fill in optional services:

```bash
cp .env.example .env
```

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `MINERU_API_TOKEN` | Optional | High-fidelity PDF parsing via MinerU API |
| `LIBREOFFICE_BIN` | Optional | Path to soffice.exe for DOCX/DOC → PDF conversion |
| `DEEPREVIEW_ROOT` | Optional | Path to DeepReviewer checkout for PDF appendix export |

No provider API keys belong in this repo's configuration — the host agent manages its own model credentials.

## Install the skill

Install to Codex, Claude Code, or both:

```bash
# Personal install — both platforms
python skills/paper-review-committee/scripts/install_skill.py --target both --scope personal

# Project-level Claude Code install
python skills/paper-review-committee/scripts/install_skill.py --target claude --scope project --project-root .
```

## Prepare evidence

The `prepare_paper.py` script preprocesses a manuscript into a shared evidence bundle that the host agent and all reviewers consume:

```bash
python skills/paper-review-committee/scripts/prepare_paper.py --paper <path-to-paper>
```

This creates a run directory under `review_outputs/<paper_stem>/<run_id>/` containing:

- `evidence/normalized.md` — structured markdown with page references
- `evidence/plain_text.txt` — full plain-text fallback
- `evidence/page_index.json` — page-to-line mapping
- `evidence/source_copy.pdf` — pristine copy of the input
- `evidence/diagnostics.json` — preprocessing trace

### With journal requirements

```bash
python skills/paper-review-committee/scripts/prepare_paper.py \
  --paper <path> \
  --journal-file <path-to-requirements>
```

Journal requirements are written to `evidence/journal_requirements.md` and referenced by every reviewer and the editor.

### Revision review

When reviewing a revised manuscript and response letter:

```bash
python skills/paper-review-committee/scripts/prepare_paper.py \
  --paper <revised-paper> \
  --revision-file <response-letter> \
  --previous-review-file <previous-review-json>
```

## Agent workflow

1. Host agent reads `SKILL.md`.
2. Classifies the manuscript paradigm from the evidence bundle.
3. Dispatches 8 reviewer tasks/subagents (3 generalist + 5 specialist). Each writes a reviewer JSON under `reviews/`.
4. Optionally writes an editor synthesis JSON.
5. If this is a revision round, also writes a revision-response review JSON.

## Finalize

After reviewer (and optionally editor) JSON files are written to the run directory:

```bash
python skills/paper-review-committee/scripts/finalize_run.py \
  --run-dir <run_dir> \
  --editor-file <editor.json>
```

Without an editor file, it produces a draft synthesis:

```bash
python skills/paper-review-committee/scripts/finalize_run.py --run-dir <run_dir>
```

This writes `draft_no_editor_synthesis.md` instead of a full `meta_review`.

## Output artifacts

Every finalized run produces:

| Artifact | Description |
|----------|-------------|
| `final_report.md` | Full committee review report |
| `meta_review.md` / `.json` | Editor synthesis (or draft) |
| `concerns_table.csv` / `.json` | Deduplicated concern inventory |
| `final_summary.json` | Machine-readable run summary |
| `evidence/` | Normalised manuscript evidence bundle |
| `reviews/` | Individual reviewer JSON files |

The top-level convenience view is at:

```
review_outputs/按论文查看/<paper>/最新结果/
```

## Revision workflow

The revision path tracks whether each previous concern was genuinely addressed:

1. `previous_concerns.json` — extracted from the prior review.
2. `revision_claims.json` — claims parsed from the response letter.
3. `revision_assessment[]` — per-concern judgment by the revision reviewer.
4. `revision_response_review.json` — final structured assessment.

A concern is marked `addressed` only when valid manuscript evidence from the revised paper confirms the change.

## Privacy and local-file boundaries

Do not commit these to GitHub:

- Papers (PDF, DOCX, DOC)
- Response letters
- Journal requirement files
- `review_outputs/`
- `review_inputs/`
- `.env`
- `.codex/`
- `.claude/settings.local.json`, `.claude/logs/`, `.claude/cache/`

All of these are already covered by `.gitignore`.

## Development

```bash
python -m pytest
python scripts/generate_schemas.py
```

## Acknowledgements

`fusion-reviewer` was designed and implemented with reference to several open-source paper-review projects:

- **AI Reviewer** (`ai-reviewer`, Jukka Sihvonen) for the multiagent reviewer/editor decomposition used in scholarly peer review.
- [ResearAI/DeepReviewer-v2](https://github.com/ResearAI/DeepReviewer-v2) for PDF-to-evidence preparation, MinerU-based parsing, tool-grounded review flow, and final-report export ideas.
- [poldrack/ai-peer-review](https://github.com/poldrack/ai-peer-review) for multi-model peer review, meta-review synthesis, and concerns-table artifact patterns.

This repository is an independent implementation focused on a platform-neutral Agent Skill plus a deterministic core library.
