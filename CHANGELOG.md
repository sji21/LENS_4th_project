# Changelog

This repository starts from the codebase transferred from the 3rd project.
Changes made before the transfer are recorded in the
[3rd project changelog](https://github.com/sji21/3rd_project_team4/blob/main/CHANGELOG.md).
This file records the 4th project from its first change onward.

## 2026-09-09

### Added

- PATCH-009 shares all 100 development questions, 200 exact retrieval inputs, original gold review, effective requirements and version hashes in `data/eval/dev100`. Diagnostic tools read repository inputs and accept explicit output paths; corpus/model preparation remains separate. (commit: `fece8b8`)

- PATCH-009 records a frozen development retrieval run of 100 questions in two input modes, preserving raw evidence and distinguishing missing article references from retrieval omissions. Adds reproducible local diagnostic scripts; no product tuning, corpus changes, LLM evaluation or aggregate accuracy claim. See `docs/patch009-dev100-retrieval.md`. (commit: `dc3ec75`)

- PATCH-008 activates seven prepared Civil Act articles in local operating data, preserving the original 133 law chunks and keeping the 165-record base index separate from seven civil vectors. Records backups, source/structure checks, published regression and DEV retrieval limitations in `docs/patch008-civil-rollout.md`. Generated data stays outside Git. (commit: `b85fd40`)

- PATCH-007 preserves collected statute plaintext snapshots, article sources and paragraph/item/subitem structure alongside unchanged retrieval text and IDs. Adds an explicit legacy-DB backfill and review export; does not expand the corpus or update operating indexes. See `docs/patch007-law-structure.md`. (commit: `2c6db32`)

### Fixed (Bug Fixes)

- PATCH-008 prevents money nouns such as `보증금이` from matching the crack signal `금이` and incorrectly prepending Article 634. Full tests: 617 passed, 3 skipped; existing law and case regression metrics remain unchanged. (commit: `b85fd40`)

- PATCH-007 exports each newly loaded article's own source URL instead of the document's first article URL, and rolls back failed statute reloads. Full tests: 606 passed, 3 skipped, 124 subtests passed; real-data copy checks preserve all 133 article texts and retrieval fields except 129 corrected URLs on reload. (commit: `2c6db32`)

## 2026-09-08


### Changed

- PATCH-005 records dispositions for all 11 inherited Dev audit items: three label corrections already landed in the 3rd project and eight labels are retained with corpus-based reasons and answer constraints. Marks the old audit as historical; evaluation data and retrieval behavior are unchanged. (commit: `a41a403`)

### Fixed (Bug Fixes)

- PATCH-004 skips LangSmith configuration and connection checks when tracing is disabled, while missing configuration still fails when enabled. Short PDF validation test IDs avoid Windows environment-variable length errors without reducing the oversized-file test input. (commit: `2ec2329`)
  - Validation: `.venv/Scripts/python -m pytest -q` on Windows: 564 passed, 3 skipped, 58 subtests passed. Skips are two disabled LangSmith checks and one private-PDF integration test; no failures or errors.

### Added

- PATCH-006 ports conditional Civil Act retrieval from 3rd-project PATCH-033, adds Article 632 alongside the sublease rule, and stores seven reviewed articles in a separate dense index to preserve existing rankings. Includes corpus ingestion, missing/mixed-index checks, regression tooling and setup instructions. Published regression sets are reused; independent evaluation and operating-data rollout remain pending. (commit: `4b9a929`, `108ca80`)

- PATCH-003 adds a fixed service baseline CLI for statute and case regression datasets, with separate Hit/Recall/MRR metrics, per-question ranks, input hashes, explicit exclusions and index consistency checks. Existing outputs and missing gold labels fail safely. All four figures it measures match the PATCH-002 baseline, and statute Dev repeated identically across three consecutive runs. (commit: `fbc946c`, `68494dc`, `8af0f25`, `3c9cce7`, `dfaa323`)

## 2026-09-07

### Changed

- PATCH-001 initializes the record files for the 4th project. `LIST.md` now holds the 4th project patch table plus the patch numbering rule, the 3rd project records with the transfer base commit, and the retrieval follow-up tasks carried over from the 3rd project. Documents transferred from the 3rd project carry a provenance note so their `PATCH-XXX` references stay unambiguous. Both changelogs start from the 4th project's first change instead of repeating the 3rd project history. (commit: `6256704`, `6580f4a`)
- PATCH-001 marks the README as a 4th project repository and states that the documented features and evaluation results are the baseline inherited from the 3rd project, not results re-measured in the 4th project environment. (commit: `6580f4a`)

### Added

- PATCH-002 records the 4th environment baseline run in `docs/eval-patch002-baseline-run.md`. The transferred codebase starts and reproduces every retrieval figure the 3rd project recorded: statute Dev Hit@3 100.0%, statute holdout Hit@3 94.4% (17/18), case Dev Hit@2 92.3% (12/13) and case substitute holdout Hit@2 87.5% (7/8), measured on the same 165-chunk corpus, the same KURE-v1 index and the same evaluation sets. The full test suite reports 547 passed with no product-code regression; the three remaining failures are existing test-configuration and platform-compatibility issues registered as PATCH-004. Official-guidance retrieval is recorded without an accuracy figure, and the holdout runs are framed as reproduction and regression checks rather than a new independent evaluation. (commit: `2bb82a3`, `8809e56`)
