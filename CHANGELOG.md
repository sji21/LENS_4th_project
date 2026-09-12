# Changelog

This repository starts from the codebase transferred from the 3rd project.
Changes made before the transfer are recorded in the
[3rd project changelog](https://github.com/sji21/3rd_project_team4/blob/main/CHANGELOG.md).
This file records the 4th project from its first change onward.

## 2026-09-13

### Changed

- Renumber the local law-expansion work from PATCH-026 to PATCH-027 because team PR #24 has published PATCH-026. Resume the same local PATCH-027 work previously folded into local 26; do not reuse it for an unrelated task. Preserve capture paths/hashes and historical commits; rename only the active branch and current records. (commit: 052af96)

- PATCH-027 compares six general-law alternatives and four civil-selection alternatives against the fixed 204-article/235-input capture. Question complete retrieval reaches 37/75, but every general alternative loses at least eight prior TOP3 targets by input; no policy passes the predeclared adoption gate. Removing civil topic selection worsens DEV retrieval, and none retrieves the nine newly required civil articles at TOP3 in their target inputs. Preserve traces, fixed denominators, regressions and replay validation; operating data and ranking policy remain unchanged. PATCH-027 stays in progress. (commit: f079a0f)

- PATCH-027 adds 61 verified article records to a 204-article candidate and evaluates 235 identical inputs with all 26 Civil Act articles searchable. Existing version members, article bodies, source/structure data and vectors are preserved. DEV fixed-target data gaps drop from 32 to 0; complete retrieval rises 28→32 / 30→34, but 16 inputs lose prior required evidence. General TOP3 loss affects 13 inputs; civil TOP3 loses 3. Full-corpus ranks and source checks are reproducible offline. Operating adoption is deferred pending ranking improvements; historical/guide/appendix coverage and LLM answers are not claimed complete. (commit: be56037, 598ced4; analysis: 1758a7f)

- PATCH-027 prioritizes completing the reviewed DEV100 law corpus before further five-article tuning. The inventory identifies 27 missing fixed-required articles plus 17 other reviewed references, with six law-tagged sources requiring document/article/version identification. These are collection candidates, not newly ingested or legally reverified records. Gold and operating data remain unchanged. (commit: f3c0728)

## 2026-09-12

### Changed

- PATCH-027 combines selection, candidate ingestion, retrieval improvements and evaluation for the same five residence/tax procedure articles. The two local experiments are consolidated without rewriting commits and their trace paths are preserved. Initial full/tax/residence ingestion loses prior TOP5 evidence on 6/4/1 inputs. The partitioned lexical candidate reproduces all 235 product outputs, raises DEV full targets 28→29/30→31 and preserves prior TOP5 evidence, but loses one TOP3 anchor. This patch remains in progress and operating data adoption is deferred. (commit: 8ed2733, b40834f, 54013ac, 0a57247, 6085564, 34ea1f0, 473dccc; consolidation: 36ca04a)

### Fixed (Bug Fixes)

- PATCH-025 rejects PATCH-015 baseline captures outside the historical checkout whose candidate trace contract they reproduce, avoiding false candidate-stage diagnostics on newer retrieval paths. (commit: 69610f5)

- PATCH-025 keeps Civil Act member rankings request-local while re-ranking the third candidate, preventing concurrent searches from mixing candidate ranks. (commit: 9fd078f)

## 2026-09-11

### Changed

- PATCH-025 preserves civil TOP2 and uses double dense weight only for the third candidate. Nine policies were compared on235 inputs; DEV context full targets29→30, Hit1 preserved and zero prior required-evidence losses. All235 live results match. Third candidates change on72 inputs; legal relevance and LLM quality are not established. No data/model changes. (commit: 23ad5bb, 0ee906e, aa2411e)

### Added

- PATCH-024 adds Civil Act articles 105/114/357 (7→10 candidates, unchanged TOP3) after 235-input comparison with zero prior required-evidence losses and unchanged general/case/guide rankings. DEV question/context complete targets rise 23→28/25→29; data gaps fall 45→32. Backed-up local adoption matches all235 candidate results. Publishes evidence and offline replay; search tuning remains separate. (commit: e640d3a, 0056ae6, 486e57f)

- PATCH-023 records a fresh main-based 235-input separated retrieval baseline: DEV question/context complete fixed targets 23/25 of100, data gaps45, held-target misses7/5, unscored25. Civil required track covers civil29/29 and all law28/29. No production changes. (commit: ee6c55a)

- PATCH-021 separates civil_laws (up to3 retained-and-filled candidates) from general law slots and carries civil evidence through staged retrieval, prompt, answer and citation/source validation. Existing generation general-law3/case2 limits stay unchanged. All235 live inputs match PATCH-020 and case/guide rankings are preserved. TOP3 DEV-059 context miss remains known; automatic irrelevant-civil exclusion is not implemented. (commit: 35be974)

- PATCH-020 compares retaining existing civil picks and filling RRF candidates at budgets 2 through 7. On the public development inputs, budget4 covers all held required civil targets without losing prior evidence, versus budget7 for RRF alone. General TOP5 stays fixed. The provisional handoff budget is TOP3 based on the user-reported LLM constraint; DEV-059 context remains a known miss for later tuning. Operating integration and applicability judgment remain unvalidated. (commit: e83976f)

- PATCH-018 tests separate general TOP5 and civil channels on the same 235 inputs. General evidence is preserved, but civil TOP2 substitution loses prior civil evidence in 10 DEV inputs; unrestricted delivery raises exposure. Shares fixed comparisons and replay artifacts; production adoption is deferred. (commit: de628b3)

- PATCH-017 compares three predeclared rank-fusion weights using the saved 235 inputs. All three fail the adoption gate: the conservative weight adds no benefit, while stronger weights improve new civil items but displace required evidence in existing inputs. Shares per-input rankings and loss diagnostics; production behavior is unchanged. (commit: a55d9c0)

### Fixed (Bug Fixes)

- PATCH-023 removes blank lines inside the LIST.md patch table so PATCH-020 onward renders as table rows. (commit: 88bf3c3)

- PATCH-022 also accepts dated standalone former-article relocation notes; held decree articles 7 and 8 retain valid paragraph recognition. (commit: dcb71c0)

- PATCH-022 validates all paragraphs in article-linked lists/ranges and preserves explicit paragraph structure across recognized dated history notes, while still rejecting mixed article headings. (commit: e226c2e)

- PATCH-022 fixes F-06 paragraph validation: require the exact named law/article and explicit paragraph structure in that evidence. Cross-references, quoted numbering, ambiguous excerpts and unnumbered text cannot establish paragraph existence. Includes civil evidence and chain/graph regression coverage. (commit: 47d492d)

- PATCH-021 migrates the remaining public regression diagnostic to explicit scopes. Legacy mixed-TOP5 scores are not treated as expected split-channel scores; required-evidence gains/losses remain visible and mismatched questions/gold are rejected. (commit: 496e896)

- PATCH-021 uses the same explicit civil budget (k_civil=3) for evaluation calls and captured settings; verification checks legacy settings separately from the added civil budget. (commit: 5384961)

- PATCH-021 verification requires a clean committed tree, compares runner/service Git bytes with LF-normalized working bytes, and checks provenance again after retrieval. (commit: 373c465)

- PATCH-021 scores general and civil result channels explicitly; mixed published regression uses general5+civil3 union coverage without a fictitious merged rank. Reports actual civil exposure and rejects mismatched gold scopes. (commit: 4878ea4)

- PATCH-018 preserves captured source bytes and validates current dependencies after newline normalization, allowing clean Git checkouts to replay mixed-newline captures while rejecting content changes. The historical runner and original results remain preserved. (commit: 52b2d5f)

- PATCH-017 replay accepts LF/CRLF checkout differences against original source hashes while rejecting actual code changes. Original capture and experiment results are preserved. (commit: dd91fa3)

## 2026-09-10

### Changed

- PATCH-014 preserves the unmerged PATCH-012 cancellation and original 35-item draft, incorporates the revised second legal review, and defines article retrieval targets and denominators (29 primary, 1 scope-provisional, 5 diagnostic-only). Conditional evidence and answer quality remain separate; measurements are deferred to PATCH-015. Source/shared hashes document path-only and newline normalization. Product code and data are unchanged. (commit: b6a72a6, 6f498c4)

- PATCH-013 switches the primary web runtime to Django 5.2 and HTML/CSS/JavaScript, retaining existing RAG and legal validation. Adds isolated chat sessions, document upload/removal, source rendering and an extensible user model with a separate web database. Signup screens remain future work. See `docs/django-web.md`. (commit: `0eaf594`)

### Fixed (Bug Fixes)

- PATCH-016 validates the published bundle schema, required files, hashes and successful capture audit before replay. Missing manifests require explicit unpublished-local mode, which never bypasses an existing bundle or the audit. Saved experiment results are unchanged. (commit: 2e656a5)

- PATCH-011 rejects bundles missing any of the seven review originals even when their manifest entries are removed, and validates full source/target law-article identifiers rather than article numbers alone. Diagnostic results are unchanged. (commit: `8e30326`)

### Added

- PATCH-016 measures civil BM25/dense/RRF candidates for the same 235 inputs without altering final retrieval. RRF TOP2 covers civil targets in 29/29 new primary items but only 2/10 existing contextual DEV items; replacement is not adopted. Shares candidate ranks, fixed-depth coverage, provenance and replay tool. (commit: 4bb6745)

- PATCH-015 captures 235 inputs on the latest main without product changes: DEV200 rankings match the saved reference; civil primary AllRequired@3/5 is 16/29 with no missing target articles. Shares snapshot provenance, candidate/filter traces and offline reports; remaining civil misses expose candidate-entry restrictions. Related tests: 33 passed. (commit: 2841e04)

- PATCH-011 shares the reviewed DEV v2 evidence, claim-level supplements, fixed primary-law targets, and saved 200-input rankings while preserving v1. An offline diagnostic validates fingerprints and reproduces article coverage/rank results without a model or operational DB; this is not whole-answer accuracy. See `docs/patch011-dev100-v2.md`. (commit: `8c82531`)

## 2026-09-09

### Added

- PATCH-009 shares all 100 development questions, 200 exact retrieval inputs, original gold review, effective requirements and version hashes in `data/eval/dev100`. Diagnostic tools read repository inputs and accept explicit output paths; corpus/model preparation remains separate. (commit: `fece8b8`)

- PATCH-009 records a frozen development retrieval run of 100 questions in two input modes, preserving raw evidence and distinguishing missing article references from retrieval omissions. Adds reproducible local diagnostic scripts; no product tuning, corpus changes, LLM evaluation or aggregate accuracy claim. See `docs/patch009-dev100-retrieval.md`. (commit: `dc3ec75`)

- PATCH-008 activates seven prepared Civil Act articles in local operating data, preserving the original 133 law chunks and keeping the 165-record base index separate from seven civil vectors. Records backups, source/structure checks, published regression and DEV retrieval limitations in `docs/patch008-civil-rollout.md`. Generated data stays outside Git. (commit: `b85fd40`)

- PATCH-007 preserves collected statute plaintext snapshots, article sources and paragraph/item/subitem structure alongside unchanged retrieval text and IDs. Adds an explicit legacy-DB backfill and review export; does not expand the corpus or update operating indexes. See `docs/patch007-law-structure.md`. (commit: `2c6db32`)

### Fixed (Bug Fixes)

- PATCH-010 requires repair-linked payment for the new reimbursement route and avoids resolving ambiguous money requests when other payment purposes are present. Fixes the review counterexample mixing landlord repairs with tenant deposit repayment. (commit: `2360a68`)

- PATCH-010 regression tools accept explicit baseline and output paths after integrating PATCH-009's shared dataset, removing developer-specific directories. (commit: `23c52d9`)

- PATCH-010 recognizes repair-expense payment followed by requests for that money, retrieving Civil Act Articles 626 and 623 for the missed reimbursement wording. The 200-input development regression changes only the two DEV-006 inputs; case and guide rankings are unchanged. (commit: `acdabb0`)

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
