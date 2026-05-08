# Paper Review Committee Skill Package

This folder is the complete shareable Codex skill package for `paper-review-committee`.

## Included

- `SKILL.md` - main workflow instructions
- `agents/openai.yaml` - UI/default prompt metadata
- `references/roles.yaml` - reviewer/editor roles and classification gate
- `references/artifact_contract.md` - required output contract
- `references/paradigm_criteria.yaml` - paradigm taxonomy and method-review criteria
- `scripts/prepare_paper.py` - prepares shared evidence bundles
- `scripts/finalize_run.py` - finalizes reviewer/editor JSON into artifacts
- `scripts/install_skill.py` - installs this package into the current Codex home

Generated files such as `__pycache__/`, `.pyc`, review outputs, and local run artifacts are intentionally excluded from release zips.

## Install

From the unzipped `paper-review-committee` folder:

```powershell
python .\scripts\install_skill.py
```

The installer uses `$env:CODEX_HOME` when set, otherwise it installs to:

```text
~/.codex/skills/paper-review-committee
```

## Runtime Prerequisites

The skill scripts are thin adapters around the local `fusion-reviewer` runtime. A machine that runs the skill must also have:

- Python environment with `fusion_reviewer` importable, normally `conda run -n review-fusion-py313 ...`
- The `fusion-reviewer` project or installed package available; revision-response review via `--provider-profile` requires a runtime whose `fusion-review codex finalize-from-*` commands accept `--provider-profile`
- DeepReviewer dependencies available when the local normalization path needs them
- LibreOffice for `.doc` and reliable Word-to-PDF conversion
- MinerU credentials if the high-fidelity MinerU route is expected

Without those runtime dependencies, the package still installs as a Codex skill, but document preprocessing/finalization cannot fully execute.
