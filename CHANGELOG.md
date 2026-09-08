# Changelog

This repository starts from the codebase transferred from the 3rd project.
Changes made before the transfer are recorded in the
[3rd project changelog](https://github.com/sji21/3rd_project_team4/blob/main/CHANGELOG.md).
This file records the 4th project from its first change onward.

## 2026-09-07

### Changed

- PATCH-001 initializes the record files for the 4th project. `LIST.md` now holds the 4th project patch table plus the patch numbering rule, the 3rd project records with the transfer base commit, and the retrieval follow-up tasks carried over from the 3rd project. Documents transferred from the 3rd project carry a provenance note so their `PATCH-XXX` references stay unambiguous. Both changelogs start from the 4th project's first change instead of repeating the 3rd project history. (commit: `6256704`, `6580f4a`)
- PATCH-001 marks the README as a 4th project repository and states that the documented features and evaluation results are the baseline inherited from the 3rd project, not results re-measured in the 4th project environment. (commit: `6580f4a`)

### Added

- PATCH-002 records the 4th environment baseline run in `docs/eval-patch002-baseline-run.md`. The transferred codebase starts and reproduces every retrieval figure the 3rd project recorded: statute Dev Hit@3 100.0%, statute holdout Hit@3 94.4% (17/18), case Dev Hit@2 92.3% (12/13) and case substitute holdout Hit@2 87.5% (7/8), measured on the same 165-chunk corpus, the same KURE-v1 index and the same evaluation sets. The full test suite reports 547 passed with no product-code regression; the three remaining failures are existing test-configuration and platform-compatibility issues registered as PATCH-004. Official-guidance retrieval is recorded without an accuracy figure, and the holdout runs are framed as reproduction and regression checks rather than a new independent evaluation. (commit: `2bb82a3`)
