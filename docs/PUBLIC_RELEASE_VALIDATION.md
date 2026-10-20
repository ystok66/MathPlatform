# Public release validation and unresolved work

Updated: 2026-10-17. Scope: first-run API setup and public-package preparation.

## Completed evidence

| Item | Result | Boundary |
| --- | --- | --- |
| Frontend production build | PASS after one sandbox-related retry | First attempt compiled but hit spawn EPERM; owned processes were inspected before retry. |
| Separate TypeScript check | PASS | Not inferred from Next build, which ignores type errors in its configuration. |
| API PyInstaller build | PASS | Windows only. |
| Public CA packaging | FIXED | Only certifi/cacert.pem is exempted from private PEM filtering. TLS verification is not disabled. |
| Root StartMathPlatform.exe build/start | PASS | Initial automation waited for descendant services and timed out; cleaned owned services, then used process-only wait for one successful retry. |
| Service status / doctor | PASS | API/Web ready, correct ownership; SQLite integrity OK. |
| First-run modal | PASS | Automatically opens after API readiness on a no-key test copy. |
| English/Chinese switching | PASS | Setup controls observed in both languages. |
| Mock provider model discovery/search/selection | PASS | Two test models, test-chat and empty-chat. |
| Empty reply handling | PASS | Empty chat reply rejected; Save remained disabled. |
| Successful selected model test | PASS | Nonempty test reply enables Save. |
| Key edit invalidates previous success | PASS | Editing key clears discovered/tested state and disables Save. |
| Save and browser refresh | PASS | Saved provider remained, key not echoed, saved-key discovery and chat test succeeded. |
| Real provider short call | PASS | 112 advertised models; exactly one gpt-5.6-sol chat call, output cap64, reported latency2263ms. No generation, no saved real credentials. |
| Shutdown | PASS | Owned API/Web services stopped, remaining list empty; owned mock PID stopped and browser tab closed. |

The public bank snapshot initially contains 3,858 questions. Smoke work used a separate copied database and a local mock key, not the public distribution's database. The user's working dist/math-platform was not replaced or used as the smoke runtime.

## Failures preserved rather than hidden

1. The first production web build hit a sandbox spawn restriction. The later successful build does not erase that initial failure.
2. The original public preparation excluded all PEM files, including the public certifi CA bundle. Both mock and real model discovery failed before a real chat call. The fixed package restores only that known public trust bundle.
3. The initial EXE start harness used PowerShell Start-Process -Wait, which waited for child services. Its 30-second timeout log is retained. Services had started; the test harness was changed to wait for the entry process only after targeted cleanup.
4. Cold API startup can be slower than web startup, producing a temporary proxy refusal. API readiness later succeeded; this turn did not optimise the startup-wide release scan.

## NOT_RUN / partial

- Native Apple Silicon and Intel builds and runtime tests: NOT_RUN on this Windows host. Native workflow/launcher source is provided separately.
- Mac browser first-launch layout, Finder double-click, signing/notarization and oldest supported OS: NOT_RUN.
- Mac notarization: not configured. An ad-hoc-signed build is not Apple-notarized.
- Comprehensive teaching, generation/review, report, history, import/export, queue-concurrency and interruption regression suite: NOT_RUN in this first-run smoke.
- Long uptime, suspend/resume, sudden power loss, fresh second PC, Windows ARM and responsive mobile matrix: NOT_RUN.
- UI cancellation with a slow provider and ambiguous save timeout: NOT_RUN in this short smoke.
- Provider models other than the selected real model: NOT_RUN; discovery is not validation.
- Service-restart persistence after saving a key: browser reload was tested; a separate post-save backend restart was not tested.
- Public GitHub upload/Release creation: NOT_RUN; no destination repository was supplied.
- Software licence and question redistribution authorisation: not selected/established by packaging.

## Evidence locations

In the maintainer workspace, the disposable smoke runtime and fixtures are under `.tmp/first-run-smoke/`. The real-call result is `real-api-result.json`; the screenshot is `setup-success.jpg`. Finite command logs are under the operating system's temporary `math-platform-bounded` directory. These operational paths are not prerequisites for an end user.

Do not interpret the PASS rows as a claim that every feature described in README was freshly retested. Do not label an unbuilt Mac package as ready for end users.
