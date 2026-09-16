# Changelog

This repository starts from the codebase transferred from the 3rd project.
Changes made before the transfer are recorded in the
[3rd project changelog](https://github.com/sji21/3rd_project_team4/blob/main/CHANGELOG.md).
This file records the 4th project from its first change onward.

## 2026-09-16

### Added

- PATCH-051 keeps body-verified housing opposition and fixed-date priority provisions together within the existing fused candidate pool and law3/law5 budgets. Resolves the missing second required article in legacy dev-001, preserves the first hit, and excludes procedure-only and unrelated questions. Actual paired K3/K5 regression over 235 fixed inputs and 78 published questions finds no new required-evidence loss; legacy DEV complete evidence at law5 improves 23→24/24 and public HOLDOUT at law3 improves 11→12/18. PR #35 review binds generic rights, protection, safety, risk, disadvantage and effect wording to explicit deposit, tenant or fixed-date outcomes so unrelated registry, privacy and other-procedure requests retain ordinary rankings. Request-boundary tests verify that Article 3-2 paragraph 2 reaches the LLM payload; full tests at c41d86f: 2,664 passed, 3 skipped, 678 subtests. A follow-up preserves explicit tenant-rights protection questions mixed with procedures and clarifies the historical replay checkout. This follow-up receives static review only; tests and KURE are not rerun at the user's request. Real generated-answer quality remains unmeasured. (commit: 1a001f7; review: c41d86f; follow-up: pending)

- PATCH-046 improves companion evidence retrieval for explicit unpaid-tax lookup requests within the existing fused TOP20 and general5/civil3 limits. Actual KURE evaluation improves complete evidence from 43/75 to 47/75 question-only and 48/75 with context, with no new required-evidence loss or extra model calls. All 78 published regression questions retain their rankings and metrics. After merging main 7ae97ee, recognizes the new current-input dialogue boundary without inheriting prior tax scopes and preserves historical replay through a hash-verified source snapshot. Repeats all 235 inputs and 78 public questions with identical results to the first PATCH-046 run. PR #34 review fixes honor explicit national/local-tax exclusions across clauses, exclusive `national only`/`local only` choices, and certificate prerequisites while keeping certificate issuance and filing tasks on ordinary rankings; the first review rerun preserves all 235 inputs and 78 public questions. Full tests after the follow-up review: 2,534 passed, 3 skipped, 678 subtests. Gold, data, model, civil selection and generation are unchanged by this patch. Renumbers the unpublished local PATCH-042 work after a remote ID collision; frozen analysis paths are retained for integrity. (commit: a8dc8de; integration: 59f394b; review: c9079df; follow-up review: 82a4e79)

### Changed

- PATCH-050 Integrate conversation management with member rooms, source annotations, and main citation validation. Clear dialogue memory on member reset; preserve pending questions, retry identity, and drafts when rooms are created. Allow 60 seconds for planner context switching while retaining the overall call budget. Renumber conversation records to 047–049/050 while retaining main patch IDs by user approval. (commit: `1d8238f`)

- PATCH-050 Provide general guidance before one missing-fact question for personal renewal/deposit procedures. Add attributed renewal guidance as a conversation-only retrieval supplement, preserving legal validation and existing indexes. Reserve 8,192 context and 512 output tokens for conversational generation to avoid dropping instructions. A factual reply after verified guidance is saved and acknowledged before the next missing-fact question; substantive follow-ups still use RAG. (commit: `d649504`)

## 2026-09-15

### Added

- PATCH-043 adds a versioned case-only retrieval candidate with pinned DB, chunks, index and model checks; DEV policy comparison, application Top-2 evidence, and a gate against reusing exposed evaluation questions. A team handoff explains local entrypoints and required SQLite, Chroma, chunks and model files. On the latest origin/main-based Windows checkout, the full suite passes 1,777 tests with 3 skips and one pre-existing PATCH-024 frozen-report test deselected. That test previously failed on unchanged main because two recomputed metrics differ only in their final floating-point digit. Real candidate DB/model runs and a new independent holdout remain pending; sealed LENS-HO2 inputs and scores are unchanged. (commit: b63f263)

- PATCH-041 verifies the latest product retriever against a fresh source-built database on local CPU: all four channels match the adopted baseline on 235 inputs, and 78 public regression questions match the existing database under the same code. Required evidence remains 43/75 in both DEV100 v2 modes and 28/29 in CIV35; previous misses remain, with no new rebuild losses. Adds reproducible capture, integrity checks and provenance. Full tests: 1,576 passed, 3 skipped, 172 subtests passed. No retrieval tuning, corpus additions, or LLM assessment. (commit: 8a57092)

### Fixed (Bug Fixes)

- PATCH-043 preserves the product service law, Civil Act and guide channels when overlaying the case-only backend. The standalone case query still works without product corpora; the product application requires both data sets. Focused retrieval, generation and citation tests: 218 passed. The last sealed LENS-HO2 score belongs to frozen commit 462e80a and a 20-case return contract, so it is not a PATCH-043 Top-2 independent evaluation. (commit: b63f263)


- PATCH-050 Clear the composer when a question is sent, preserve a newer draft during the response, and restore failed submissions only into an empty composer. Nine JavaScript regressions pass. (commit: `d649504`)

- PATCH-041 validates recorded retrieval settings against an independent frozen contract, including search limits, RRF/depth, BM25, query expansion, weights and civil selection. Check live settings before and after capture and reject missing or altered settings during offline replay even if artifact hashes are recomputed. (commit: daf49f3)

- PATCH-041 normalizes evaluation-criteria line endings for cross-checkout replay and compares case/guide rankings by stable chunk ID alongside law article IDs. Raw source-bundle, data and model integrity checks remain enforced. Adds 10 regression cases; a fresh 235-input run preserves all four channels and scores. (commit: b2ae7eb)

### Added

- PATCH-045 adds member-owned chat rooms while keeping chat as the post-login and return landing page. A blank landing or New Chat does not create a database room; the first successfully answered member question creates and names it, and the sidebar pencil action renames the same room everywhere. A data migration removes only empty legacy auto-created default rooms while preserving any room with chat, document, calendar, checklist, fact, or report data. My Page combines all room schedules in a real monthly calendar, room-colored checklist items, and versioned PDF folders per chat room. Reports are generated from the current conversation, downloaded immediately, retained for later member downloads, and announced in the chat UI. STT UI and processing are excluded. Related Django checks: 77 passed. Existing PATCH-044 citation validation files remain unchanged. (commit: `1d8238f`)

- PATCH-041 verifies the latest product retriever against a fresh source-built database on local CPU: all four channels match the adopted baseline on 235 inputs, and 78 public regression questions match the existing database under the same code. Required evidence remains 43/75 in both DEV100 v2 modes and 28/29 in CIV35; previous misses remain, with no new rebuild losses. Adds reproducible capture, integrity checks and provenance. Full tests: 1,576 passed, 3 skipped, 172 subtests passed. No retrieval tuning, corpus additions, or LLM assessment. (commit: 8a57092)

### Changed

- PATCH-040 documents successful CUDA operations and KURE automatic/explicit GPU embeddings after the README environment setup on a new RTX A4000 Pod (driver 595.91.07, Python 3.11.15, torch 2.14.0+cu130). No CUDA code change is needed for this tested environment. Keep the earlier warning unresolved, PATCH-029 unapplied and unverified on a Pod, and new-Pod DB/search/LLM evaluation unrun. Documentation only. (commit: 7773253)

## 2026-09-14

### Fixed (Bug Fixes)

- PATCH-050 Separate procedure answer labels into paragraphs in the chat display, including saved responses, and request blank lines in generated procedure answers. Version the script URL to refresh cached clients; verify the existing conversation in the browser. (commit: `d649504`)

- PATCH-050 Pass question purpose separately from topic into retrieval and answer instructions, distinguishing procedures, timing, specific permission, definitions, documents and sources. Keep legacy proposals compatible and preserve follow-up targets. Live answer quality remains under review; see docs/chatting-question-purpose.md. (commit: `d649504`)

- PATCH-038 Fix renewal-method follow-ups being mistaken for new contract facts and rejected despite preserved context. Keep fact validation strict; verify normal, recovery, and pending-question contexts with the local model. (commit: `876e19f`)

- PATCH-038 Preserve follow-ups when the model repeats an unchanged fact using a different quote from its original user turn. Retain the saved fact and reject quotes from other turns. Full-RAG testing still shows insufficient focus on the requested notification method. (commit: `876e19f`)

- PATCH-037 isolates unsupported descriptive facts and identical statement repetitions. Rejected plans preserve consultation memory and ask for case confirmation; model connection failures leave state unchanged for retry. Follow-up questions retain their target, with clearer correction and unknown-answer instructions. Legal application and verification remain with their existing owner. (commit: `02a6434`)

### Added

- PATCH-038 Retain the latest substantive user request separately from answer context, advance follow-up focus, and keep summary targets stable. Conversational prompts prioritize the latest question and explicitly state missing evidence instead of repeating background. Add multi-turn, topic-transition, and bounded-query regressions. (commit: `876e19f`)

- PATCH-038 Provides evidence-based guidance followed by one relevant question, remembers short answers within the same consultation, and supports summary requests during clarification. Adds guided conversation and Django API regressions; legal validation remains unchanged. See docs/chatting-guided-dialogue.md for live checks and remaining answer-quality limits. (commit: `876e19f`)

- PATCH-037 Add full dialogue acceptance reports, isolated browser fixtures, and regressions for document choices and explanation query reuse. The initial full-RAG quality failures are retained; the subsequent user-scoped dialogue recovery and regression results are documented separately in docs/chatting-recovery.md. (commit: `02a6434`)

## 2026-09-13


### Added

- PATCH-036 Bound conversation model calls and context, retain request identities after uncertain responses, and exclude rejected history and sensitive exception text. (commit: `4b35221`)

- PATCH-035 Adds pre-validation conversational answer styles and Django clarification choices, rephrasing controls, status reasons, and stale-choice protection. (commit: `6c4a7d7`)

- PATCH-034 Preserves current query conditions and resolves owned document continuity, ambiguity, and separate document evidence without changing legal validation. (commit: `678d55a`)

- PATCH-033 Adds bounded field-specific clarification, persisted follow-up choices, unknown-value preservation, and explicit answer failure categories. (commit: `624971e`)

- PATCH-032 Adds opt-in conversation dispatch with original-input protection, fixed nonlegal replies, atomic state updates, and legacy recovery for rejected planner output. (commit: `f791706`)

- PATCH-031 Adds bounded single-call Qwen conversation planning, source-checked structured decisions, and development captures that distinguish raw model errors from source-preserving normalization. (commit: `5a70d95`)

### Changed

- PATCH-028 locks the resolved data target across checkouts and both source/frozen tools, while allowing independent targets to proceed. Preserve the lock file outside replaced payloads and ignore the default local lock in Git. Cross-process and focused checks:107 passed. (commit: e2872e5)

- PATCH-028 rolls back source/frozen installations on Ctrl-C, including first installs and interrupted postflight checks. Wait for mutating children before rollback and track paths before renaming originals. Record ten resolved embedding/index dependency versions to invalidate incompatible reuse. Focused tests:138 passed. (commit: 23d3b8c)

- PATCH-028 fixes schema-change detection, invalidates vector reuse on embedding/index pipeline changes, and recovers damaged owned data with explicit --rebuild. Explain unsupported source-to-frozen conversion without modifying data. Record completed Mac/RunPod installation checks, including RunPod DB/basic-search completion; CUDA compatibility remains unresolved. Focused tests:86 passed. (commit: 150e769, df08178)

- PATCH-028 streams builder progress and warnings to the terminal while preserving per-worker logs and failure diagnostics. Stop workers before releasing control on interrupted output. Focused validation:68 passed; no DB or GPU dependency changes. (commit: 587f632)

- PATCH-028 allows `--venv-dir` for a container-local RunPod environment while retaining persistent DB/model storage and default Windows/Mac `.venv` behavior. Preserve existing environments; use the selected interpreter throughout preparation/build. Document ephemeral-environment reinstallation and the completed Mac installation check; RunPod speed is not yet measured. (commit: a6cc50b)

- PATCH-028 makes source-based server construction the default: parse approved sources, build SQLite and separate indexes, reuse unchanged vectors, and expose Django `prepare_retrieval`. Keep frozen DB bundles for evaluation reproduction only; support persistent Hugging Face caches for RunPod. (commit: 9e50649, c19b4b1, 2654264)

- PATCH-028 adds an environment bootstrap (venv, required packages, verified KURE cache), explains the handoff data folder before manual setup, and supports verified first installation into an empty checkout with failure recovery. macOS runtime remains unverified. (commit: 9aadb63, e59f53f)

- PATCH-028 documents the macOS Python entry point alongside Windows commands, including environment setup and the lack of macOS runtime verification. Documentation only. (commit: c5249ad)

- PATCH-028 defaults to a single no-argument command showing DB check → apply if needed → confirmation. Prompt for the source only when needed, skip identical data and keep subprocess diagnostics in per-step logs.24 focused tests pass; retrieval/data unchanged. (commit: 6b3e43d)

- PATCH-028 completes the main-checkout rollout: general178/civil26 with235/235 matches before and after installation,375 model calls each. Repeated apply leaves all payload bytes unchanged and creates no additional backup. Preserve prior local PATCH-024 edits separately. Validation:1,135 full-suite passes,3 skips,172 subtests;20 focused passes after the copy-inspection correction. (commit: 0e80edd)

- PATCH-028 inspection now opens disposable copies because Chroma may rewrite physical index files on read. Repeat apply preserves every installed payload byte and creates no new run or backup. (commit: e8e3a61)

- PATCH-028 adds a local retrieval-data status/apply/restore command and a virtual-environment PowerShell launcher. Verify complete bundle identity and duplicate article/chunk keys, skip identical installations, serialize writers, and automatically restore after postflight or receipt failures. Main-folder rollout verification is pending. (commit: b3669e1)

- PATCH-027 unifies active source, test and data directory names under 027. Preserve all frozen source/capture bytes and hashes; translate only the three old data prefixes on read, leaving execution-code hashes tied to their original commits. Product retrieval and installed data are unchanged. (commit: 36e9791)

- PATCH-027 integrates main `6e40d21` after team PR #24 merged during review. Preserve both patch records and distinguish the legacy civil10 setup from the expanded civil26 profile in README. Retrieval code is unchanged; the integrated checkout passes 1,111 tests with 3 skips and 172 subtests. (commit: 7be0613)

- PATCH-027 fixes PR #25 receipt-failure rollback: validate and store the installation receipt inside the protected transaction, so missing, partial or invalid writes restore the original payload without requiring receipt-based recovery. Rename explanatory documents to PATCH-027, update links and mark old experiments explicitly while preserving captured script/data paths and hashes. Retrieval policy and active data are unchanged. (commit: 562905d)

- PATCH-027 completes product rollout in the patch worktree: back up the 143-article data and activate the verified 204-article/civil26 profile. Both preflight and post-install default-factory runs match all 235 final-test outputs across four channels, with 375 model calls each. Publish capture/backup records and setup/restore instructions; 1,042 tests pass, 3 skip and 124 subtests pass after activation. Main-checkout/server data remain unchanged until their separate post-merge rollout. (commit: 4ddf8cf)

- PATCH-027 connects the selected expanded-law policy to the product factory and BM25 fallback through a corpus/index-verified local profile. Preserve the general5/civil3/case5/guide2 API, report active evaluator settings and share embeddings only within a request. Add staged 235-input product verification and byte-verified backup, transactional replacement and restore tools. Worktree rollout verification is pending. (commit: 15a3461, 4457916)

- PATCH-027 completes 705 fresh service calls and selects the record-lookup candidate for the 61-article expansion under the new user-authorized net-benefit protocol. DEV complete retrieval is 28→43/75 questions and 30→43/75 contexts; general3+civil3 coverage is 27→40 and 28→38/75. The last refinement recovers one prior loss, leaving seven returned-target and seven general TOP3 loss inputs. Model calls rise 235→375 and mean latency 0.232→0.403s. Preserve the remaining regressions and verified sources; 1,014 tests pass, 3 skip, 124 subtests pass. Technical adoption is recommended; production policy wiring and operating-data rollout remain pending. (commit: d9ee777)

- PATCH-027 prepares the user-authorized final test for adopting the 61-article expansion. Freeze net gains, consumed evidence coverage, CIV35 preservation and integrity checks instead of requiring zero historical losses. Compare the verified policy with one bounded fixed-date-record lookup refinement using 235 fresh operating/control/candidate searches. Earlier acceptance results remain unchanged. (commit: 7294492)

- PATCH-027 explains the union of eight returned-target and seven general TOP3 loss inputs: eleven distinct inputs, five complete-to-incomplete regressions, with all missing targets still present among candidates. One offline seedless-RRF comparison across 235 inputs recovers DEV-059 and gains DEV-064 but loses DEV-058; question complete retrieval rises 43→44/75 and context stays 42/75. Preserve both gains and losses, paired-context diagnostics and replay checks. No operating change or retrospective acceptance-gate revision. (commit: 4da57fd)

- PATCH-027 corrects same-edition Civil Act reference parsing/revalidation, notice negation versus failed delivery, and public/private rental context separation. Freeze `context_both + context_reference` and verify 235 fresh service outputs against recombined rankings, bodies, sources, cases and guides. DEV complete retrieval remains 43/75 questions and 42/75 contexts, with nine new required civil articles retrieved; civil question Hit3 is 23/32 rather than the earlier 24/32 preview. Eight prior-return and seven general TOP3 loss inputs remain, so adoption is withheld. Measured model calls increase 235→375; the zero-additional-call claim is withdrawn. Share the clean-commit capture and replay guards; 988 tests pass, 3 skip, 124 subtests pass. Operating data and retrieval remain unchanged. (implementation: 5ea42a8, f8597cd; verification: dd47e58)

- PATCH-027 compares concept expansion on the same 204 articles and 235 inputs. Expanding both BM25 and KURE raises civil Hit3 from 13→23/32 (question) and 13→20/32 (context), retrieving eight of nine newly required civil articles, but introduces two new evidence-loss inputs against the untuned 204-article result. Lexical-only civil expansion retrieves seven new articles without new returned-target losses against that result. General-law both-channel expansion improves Hit3 28→32/47 and 28→33/47, but loses prior targets; hard TOP3 preservation blocks newly added TOP3 gains. No policy passes the fixed adoption gate. A return-mail concept false positive was narrowed before final recapture. Operating retrieval/data remain unchanged. (commit: bf8b6eb, 082d3d7; analysis: 0fd54bd)

- Renumber the local law-expansion work from PATCH-026 to PATCH-027 because team PR #24 has published PATCH-026. Resume the same local PATCH-027 work previously folded into local 26; do not reuse it for an unrelated task. Preserve capture paths/hashes and historical commits; rename only the active branch and current records. (commit: 052af96)

- PATCH-027 compares six general-law alternatives and four civil-selection alternatives against the fixed 204-article/235-input capture. Question complete retrieval reaches 37/75, but every general alternative loses at least eight prior TOP3 targets by input; no policy passes the predeclared adoption gate. Removing civil topic selection worsens DEV retrieval, and none retrieves the nine newly required civil articles at TOP3 in their target inputs. Preserve traces, fixed denominators, regressions and replay validation; operating data and ranking policy remain unchanged. PATCH-027 stays in progress. (commit: f079a0f)

- PATCH-027 adds 61 verified article records to a 204-article candidate and evaluates 235 identical inputs with all 26 Civil Act articles searchable. Existing version members, article bodies, source/structure data and vectors are preserved. DEV fixed-target data gaps drop from 32 to 0; complete retrieval rises 28→32 / 30→34, but 16 inputs lose prior required evidence. General TOP3 loss affects 13 inputs; civil TOP3 loses 3. Full-corpus ranks and source checks are reproducible offline. Operating adoption is deferred pending ranking improvements; historical/guide/appendix coverage and LLM answers are not claimed complete. (commit: be56037, 598ced4; analysis: 1758a7f)

- PATCH-027 prioritizes completing the reviewed DEV100 law corpus before further five-article tuning. The inventory identifies 27 missing fixed-required articles plus 17 other reviewed references, with six law-tagged sources requiring document/article/version identification. These are collection candidates, not newly ingested or legally reverified records. Gold and operating data remain unchanged. (commit: f3c0728)

## 2026-09-12


### Added

- PATCH-030 Defines a bounded planner input/output contract with source quotes, owned documents, numeric and explicit-negation checks; invalid decisions preserve the original session state. (commit: `9f79809`)

- PATCH-029 Adds session-scoped user statements with quoted provenance, corrections, topic boundaries and private answer memory; document deletion and expiry clear derived context. (commit: `02a030f`)

- PATCH-049 Adds a synthetic conversation capture runner, explicit non-observable checks, source fingerprints, latency and model-call measurements with predeclared acceptance gates. Records 23 real-model baseline turns and synchronizes local data with the already reviewed 10-article Civil Act corpus. (commit: `3b6986a`)

- PATCH-048 Adds separate development and acceptance conversation scenarios with per-turn action, fact and query expectations; infrastructure checks remain distinct from legal-answer accuracy. (commit: `411d3ec`)

- PATCH-047 defines the conversational chatbot roadmap on `chatting-upgrade`: twelve sequential patches with planned child branches, acceptance criteria, test scenarios, a baseline and per-feature review before integration. Product behavior is unchanged. See `LIST.md`. (commit: `6c091a8`)
- PATCH-039 matches uploaded-document law citations by law/article/branch identity instead of display text containing conjunctions. Preserve original issue text, exact chunk attribution, and rejection of absent or similarly numbered citations. Related 546 passed, 16 subtests; full UTF-8 suite 1,536 passed, 3 skipped, 172 subtests. Explicit incorporation relationships and live 27-question outcome/cost evaluation remain separate follow-ups; current relation policy is unchanged. (commit: fbc3500)

- PATCH-039 shares copied-title and emphasis parsing across article validation, paragraph validation and display spans. Only matching retrieved titles are excluded; independent claims remain checked. Preserve correct evidence links with split emphasis, reject wrong-law fallback, and support nested-title URLs. Related 482 passed, 16 subtests; full UTF-8 suite 1,467 passed, 3 skipped, 172 subtests. (commit: 6e3064a)

- PATCH-039 identifies retrieved laws from the citation head, excluding article references inside the parenthesized title. Share this identity with paragraph/header validation while rejecting ambiguous trailing sources. Related 387 passed; full UTF-8 suite 1,434 passed, 3 skipped, 172 subtests passed. (commit: 3d8355b)

- PATCH-039 checks each adjacent law citation independently instead of absorbing an earlier article into the next law name. Preserve plain/emphasized citations, conjunctions, multiword law names, and paragraph offsets. PR #27 P1 regression fixed; related 366 passed, full UTF-8 suite 1,413 passed, 3 skipped, 172 subtests passed. (commit: ce37159)

- PATCH-039 separates law-name context from retrieved article provenance: body cross-references no longer authorize unretrieved citations. Paired Markdown emphasis is masked for citation/paragraph parsing with original offsets and issue excerpts preserved. Related tests: 225 passed, 6 subtests passed. Full suite with Python UTF-8 mode: 1,272 passed, 3 skipped, 172 subtests passed. Default Windows cp949 mode fails an unchanged frontend test's file read; no live LLM evaluation was performed. (commit: 88e23db)

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
