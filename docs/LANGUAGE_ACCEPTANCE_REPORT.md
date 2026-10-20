# Unified language acceptance / 统一语言验收报告

Updated: 2026-10-17. Overall: **BLOCKED / STOPPED_AFTER_TIMEOUT**.

This status supersedes earlier in-progress summaries. This is not an all-feature PASS and is not approval to publish the candidate.

## 1. 当前结论

- 已修复 Windows 查询参数引号、UTF-8 BOM 兼容、练习刷新后题目错位，以及部分动态语言和助手草稿问题。
- 最新选定离线测试集 101 项通过；共享语言检查为 2715 个键、23 个 prompt 版本；独立 TypeScript 检查通过。
- API 与前端重新构建成功，但最后一轮构建产物尚未同步到正在验收的 R2 包。
- 真实 API 测试发现并发报告写数据库时出现 `database is locked`，并返回 HTTP 500。
- 题库导入 dry-run 触发 60 秒请求超时。按照超时规则停止后续流程，没有自动重试、重置额度或继续打包发布。
- 隔离服务已通过 launcher 停止；随后 status 显示 process_count=0，3000/8001 不被本包或其他进程占用。
- 停止后的隔离数据库 `PRAGMA integrity_check` 为 `ok`。这不等价于所有业务记录均正确。
- 正式 `dist/math-platform`、正式题库、学习记录、密钥和先前公开 ZIP 未被本轮验收覆盖。

## 2. 隔离与预算

| Item | Observed value |
|---|---|
| Candidate under test | `dist/math-platform-language-candidate-20261017-r2` |
| Private evidence | `.tmp/language-acceptance-20261017` |
| First inference | 2026-10-17 19:51:49 UTC |
| Original deadline | 2026-10-17 21:21:49 UTC |
| Hard call limit | 160 actual external inference attempts |
| Attempts at stop | **87 / 160** |
| Responses received | 56; not equivalent to 56 valid business results |
| HTTP errors | 30, mainly provider `/chat/completions` 404 |
| Transport errors | 1 |
| Usage-bearing responses | 56 |
| Reported prompt tokens | 753384 |
| Reported completion tokens | 57259 |
| Reported total tokens | 810643 |
| Dispatch status | Stopped, durable budget flag and STOPPED.json set |

Token counts are provider-reported on received responses only. Failed requests may still incur costs. No price estimate is presented as a billing statement. Per-case inference deltas overlap when other tests run concurrently; the durable ledger total is authoritative.

Saved models were retained: most roles use `gpt-5.5`, project/recovery agents use `gpt-5.6-sol`, and lower-star generation uses `gpt-5.6-terra`. No model was silently replaced with a mock.

## 3. 已完成修改

- Shared `packages/i18n` registry, locale resources, frontend resource generation/checking, language settings endpoint, UI/output language separation and headers.
- Backend request language priority, unsupported-language validation, persisted job/task language snapshots, fail-closed missing prompt locales, and budget accounting at external dispatch.
- Language-aware reuse/idempotency boundaries and assistant confirmation snapshots in covered paths.
- Practice refresh preserves the current session question rather than replacing it with the first question-bank result.
- Normal practice adds previous/next navigation, and help/self-check/system-generated task titles use shared translations in covered paths.
- Assistant source fixes preserve valid plain-text answers and correctly recognize explicit no-reuse requests. These latest changes passed offline tests but were not real-API retested in the final binary.
- Frontend source fixes the language/header overlay that covered the assistant close button. Final build succeeded; final packaged visual retest remains NOT_RUN.
- Windows acceptance scripts use UTF-8 BOM where required by Windows PowerShell and quote query paths containing `&`.

This is a partial migration, not a claim that every legacy string or inline prompt branch has been converted to semantic keys.

## 4. 验收矩阵

| Area | Status | Evidence / boundary |
|---|---|---|
| Selected offline tests | PASS | Latest combined suite: 101 passed. Earlier 97/103 counts are overlapping selections and are not added together. |
| Independent TypeScript check | PASS | Executed independently; Next build itself skips type validation. |
| Locale key/interpolation/prompt-file gate | PASS | 2715 keys, 23 prompt versions. File parity is not semantic equivalence proof. |
| API and frontend final builds | PASS | `language-api-final`, `language-web-final` bounded logs. Not yet deployed to R2. |
| R2 launcher start/status/doctor | PASS | API/Web readiness and ownership observed before tests; DB integrity OK. |
| Stop and cleanup | PASS | Launcher stop removed only R2 services; subsequent process_count=0. |
| Model discovery | PASS | 113 models returned, including configured models. |
| Real connection tests | PARTIAL | Successful English/Chinese/terra tests; earlier provider 404 preserved. |
| Normal generation EN/ZH | PARTIAL | English batch completed with 1 accepted and 1 rejected slot; Chinese batch completed with 1 accepted. Provider failures and an invalid mathematical candidate were rejected. |
| English generated content | PASS, one manual sample | 6x6 grid selection sum 111; complete elementary English steps inspected. This is not a difficulty-calibration certificate. |
| Generated difficulty metadata | FAIL | An accepted sample stored zero total-score/empty reason despite nonzero star metadata; provenance consistency requires follow-up. |
| Cross-language generation queue | PASS, observed scenario | English batch ran while Chinese batch waited; then progressed. No 8+8 stress claim. |
| Ordinary practice and wrong-answer submission | PASS, narrow | Three-question session; deterministic wrong answer recorded; real AI review in EN/ZH. |
| Refresh selection mismatch | PASS, narrow UI retest | Current second question remained selected after refresh, rather than changing to an unrelated bank question. Correct-answer UI submission after the last overlay fix is NOT_RUN. |
| Strict tests from bank | PASS, narrow | EN/ZH three-question strict sessions; help endpoint denied with 403 and no inference. Full star-ratio audit and AI gap filling are NOT_RUN. |
| Single similar-question idempotency | PASS, observed scenario | Two identical-key calls returned the same intervention/job; one generated variant was appended. |
| Batch similar questions EN | PASS, narrow | Completed 2/2 accepted and a practice session was created. Full content/difficulty review not completed. |
| Batch similar questions ZH | PARTIAL | Terminal partial state: 1/2 accepted, one rejected; practice created for accepted item. |
| Recovery Agent EN/ZH | PASS, bounded diagnosis only | `gpt-5.6-sol`, max_loops=1, loop_count=1, completed without error. Cancel was called after terminal completion; mid-flight interruption is NOT_RUN. |
| Conventional recovery | PASS, request only | Real endpoint returned; comprehensive stale-state fault injection remains NOT_RUN. |
| Reports object selection | PASS, UI | Dropdown includes current practice/batches/history. Selecting the actual session showed one recorded answer. |
| AI reports | FAIL / PARTIAL | Real successful summaries exist, but provider 404 fallbacks and concurrent SQLite-lock HTTP 500s occurred. Not all report types passed. |
| Initial report data scope | INVALID_TEST_SCOPE | Earlier cases used learner ID `default` instead of actual `default-learner`; empty evidence in those cases is not proof of a product statistics bug. Corrected cases retained separately. |
| Report-to-generation draft | PASS, narrow | Read-only draft endpoint returned; complete confirm/start/cancel loop is NOT_RUN. |
| Project assistant | FAIL / PARTIAL | Real calls exposed generic fallback replacing text, no-reuse ignored, and incomplete post-tool answers. First two source defects fixed but final binary retest NOT_RUN; post-tool synthesis remains incomplete. |
| Help / hint generation | NOT_RUN, AI path | Help-next returned deterministic help. This must not be counted as AI hint generation/review/submission coverage. |
| Solution explanation EN/ZH | PASS, narrow | Real responses in selected language, no provider error; arithmetic checked on the apple-pricing sample. |
| Question concern | PARTIAL | Chinese real response succeeded; English provider 404 fallback failed. |
| Quality detection EN/ZH | PASS, 1 fixed case each | Both evaluated only `star-calibration-1-basic-fraction`; not the complete quality suite. |
| Repair suggestion | PARTIAL | English real no-change-needed result supported by arithmetic; Chinese provider 404 failed. Publishing a repair and independent repair review are NOT_RUN. |
| Tag/question filters and learners | PASS, narrow | API list/filter and isolated learner creation executed; exhaustive combinations/UI accessibility not verified. |
| History export | PASS, export only | Private JSON about 7.4 MB was produced. Round-trip final evidence incomplete. |
| Question-bank export | PASS, export only | Private JSON about 58.7 MB, 3864 questions and 44653 question-tag rows. |
| Import dry-run | FAIL, timeout | Request exceeded 60 seconds during transfer test. The runner aborted and did not write a final round-trip summary. Do not infer full import success from partial progress. |
| Backup/restore and destructive protection | NOT_RUN | No full restore or dynamic authorization/security matrix completed. |
| Visual coverage | PARTIAL | Seven English pages inspected in R1, key practice/report behavior checked in R2. Full Chinese page/popup and narrow-screen coverage not completed. |
| Final sanitized Windows/source ZIP | NOT_RUN | Preparation script exists, but timeout gate stopped deployment/packaging. |
| GitHub upload / Mac cloud build | NOT_RUN | Explicitly outside this run. |

## 5. 新发现的阻塞问题与原因

### SQLite transaction held while waiting for a model

Observed: report requests fail at `reports.py` commit with SQLite `database is locked` while similar-question generation is running.

Source evidence: `routers/mastery_variants.py` adds generator logs through `add_ai_logs`, which calls `db.flush()`, then calls the reviewer model without a commit between those operations. The write transaction can therefore remain open during the external model wait. This explains a concrete contention path; it is not proof that every locking path has been found.

Proposed correction: persist generator logs/progress and end the transaction before calling the reviewer; retain cancellation and late-result publication guards. Release report read transactions before external inference as appropriate. Test simultaneous report/variant operations once after an offline regression.

### Dry-run import uses database writes and does not have bounded progress

Observed: the large question-bank dry-run timed out at 60 seconds. The exported bank contains tens of thousands of tag relationships.

Source evidence: both import routes iterate rows with `db.merge()` and `db.flush()`, including dry-run; `finish_import_transaction` only rolls back at the end. This means preview can hold a database write transaction and perform many per-row queries. Active-task protection is not evident at the start of these routes.

Proposed correction: make preview genuinely non-mutating and bounded; add active-task/import coordination; avoid per-row round trips for bulk unchanged rows. Do not merely raise the timeout and call it fixed.

### Other remaining issues

- English batch-variant queue message still contains Chinese in its persisted progress payload.
- Some report selection notifications retain system-generated Chinese labels.
- API-key presence/readiness wording can imply successful model calls despite actual provider 404s.
- Several form controls need clearer accessible labels; singular/plural text remains imperfect.
- Assistant knowledge search/post-tool synthesis, output-language application in all UI draft types, intervention thread propagation, and complete task retry/restart language isolation remain unverified or incomplete.
- Some release-manifest warnings include mutable test configuration/data changes; these must be separated from immutable program hash failures.
- Generated Chinese answers, all prompt semantic rules, and comprehensive difficulty preservation were not manually reviewed end-to-end.

## 6. 构建、证据与交付边界

- Running test package was R2; it is private because it contains the real test configuration and isolated test history. Do not upload that folder.
- Final API artifact exists in `.tmp/pack-api-dist/math-api`; final web build exists in `apps/web/.next`. Their existence/build success does not mean they have been installed into a tested release.
- `scripts/pack/prepare_language_candidate.py` was added for a separate sanitized candidate directory and ZIPs. It has not been executed or validated.
- No new sanitized candidate ZIP or formal release promotion was completed after the timeout.
- The previous report is preserved as `.tmp/language-acceptance-20261017/report-before-timeout-stop.md`.
- Budget evidence: `inference-budget.sqlite`, `FINAL_BUDGET_SNAPSHOT.json`, `STOPPED.json`.
- Per-case evidence: timestamped JSON files in the same private directory. Raw/private transfers and provider logs must not enter public packages.
- Bounded command logs are in `%TEMP%/math-platform-bounded`, notably `language-transfer-real-20261017-163709-184.*` and the build/test log groups.
- UI stop evidence: `reports-en-stop-evidence.jpg`, showing the API-unavailable restart guidance and zero running/queued tasks after cleanup.

## 7. 下一步门禁

Further paid execution requires resolving the timeout stop and explicit continuation approval. Do not remove the stop flag or reset the original deadline silently. Fix the two database/transfer defects, run offline regressions, then perform one bounded targeted retest if the original budget window remains. Otherwise report real-API retest as NOT_RUN and request a new explicit budget before another paid run.

## 8. Targeted repair update / 专项修复进展 (2026-10-17)

The following supersedes the earlier statement that the two identified transaction/preview defects were still unmodified. Full real-API acceptance remains stopped; the durable 87-call ledger and original deadline were not reset.

### Source changes

- Added a shared external-model transaction boundary. Batch variant generation/review, report generation and recovery-agent inference commit pending local work before waiting on the remote provider. Existing post-response cancellation checks remain in the publication path.
- Import preview now uses bulk read-only table indexes and in-memory conflict/reference checks. It does not call merge, flush, commit, rollback-as-preview, or audit writes. Limits are 200,000 rows and a cooperative 20-second processing deadline; HTTP JSON parsing/database query latency are not a hard process-level cancellation guarantee.
- Actual imports now reject known active/queued AI work and use a process-local import mutex. This is not a cross-process/global maintenance lock. A full asynchronous formal-import progress/cancellation workflow is still NOT_IMPLEMENTED in this repair.
- Diagnostic history now returns report language, UTC timestamps, snapshot time, safe action count and a diagnosis-completion status. The browser prefers a recent report in the interface language and allows choosing another saved report without translating it or calling AI.
- Completed/no-action diagnostics are labelled as no repair performed. Executed safe actions are not presented as verified resolution. Unknown historical report language remains unknown.
- Recovery requests explicitly snapshot AI output language; deterministic fallback text and background execution use that snapshot.

### Offline evidence

- New targeted tests plus existing question-bank import tests: **23 PASS in 11.18 seconds**.
- Actual private export fixture: **73,837 rows**, about **58.7 MB**, preview **2.717 seconds**, with the isolated SQLite database opened in read-only mode.
- Synthetic 45,000 additional question/tag relationships also passed with query count bounded by table count rather than row count.
- SQLite regression: a second independent connection successfully writes while the fake external-provider callback is running. This is an offline transaction test, not a paid-model concurrency result.
- Independent TypeScript check PASS; shared language check PASS with **2738 keys / 23 prompt versions**.
- API and frontend builds PASS. First frontend attempt failed with Windows spawn EPERM; one bounded retry succeeded. The failed build PID and direct children were subsequently confirmed absent.
- Test harness collection initially collided with an unrelated installed tests package, then Windows temporary-directory ACLs masked test details. The fixture was made self-contained; a bounded run with usable temporary-directory permissions passed.
- The first repair sync wrapper incorrectly treated an unset PowerShell LASTEXITCODE as failure after successful copies. The wrapper was corrected; its bounded retry succeeded.

### Remaining validation

- Packaged no-inference HTTP checks and visual checks are pending at this update.
- Paid-model concurrent report/variant retest remains NOT_RUN; no stop flag has been removed.
- Formal-import performance, multi-process maintenance exclusion, complete preview/import conflict parity, and the larger original all-feature matrix are not certified by these 23 tests.

### Packaged no-inference checks (completed)

- Updated private R2 API EXE, frontend resources and shared locale resources. Launcher `doctor --json` returned `ok=true`, `services_ready=true`, database integrity `ok`, and a valid `language-repair-candidate-20261017` release manifest (4282 files).
- Actual HTTP preview on the packaged API passed for both languages: EN **4.718 s**, ZH **3.079 s**, preserving expected counts (3864 questions / 44653 question-tag links).
- Packaged diagnostic-history API passed preferred-language ordering, snapshot metadata, explicit no-repair status and omission of raw input snapshots/steps from the history list.
- Durable inference count before and after these checks: **87**, added calls **0**; the stopped budget flag stayed set.
- Evidence: `.tmp/language-acceptance-20261017/REPAIR_HTTP_RESULTS.json` and `language-repair-doctor` bounded logs.
- Visual retest is **BLOCKED**: the previous browser tab was stale, and the current tab was an internal browser error page. Browser safety policy rejected access to that page. No workaround or alternate browser surface was used. The user was asked to manually refresh the localhost page.
- Formal `dist/math-platform` and public ZIPs remain unchanged. R2 is a private acceptance package containing test configuration/history, not a sanitized distribution.

## Sanitized packaging update / 脱敏打包说明

Package version: `language-repair-candidate-20261019`. Program resources come from the repaired R2 candidate; this packaging operation does not rebuild the binaries.

A new bank-only database and matching source-bank snapshot are exported from the earlier clean public distribution. API keys are blank; non-bank tables are empty; private test screenshots and runtime logs are not copied.

The earlier NOT_RUN entry for ZIP creation is historical. The packaging outcome and archive hashes are recorded separately in CANDIDATE_REPORT.json. No new functional tests, paid API calls, GitHub upload or Mac build are performed by this packaging step. Remaining acceptance failures and NOT_RUN cases above are unchanged.
