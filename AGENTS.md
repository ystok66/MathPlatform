# Windows Execution And Timeout Rules

This project runs on Windows and includes long-lived API/Web services. Follow
these rules before running tests, builds, packaging, or release smoke checks.

## Forbidden Actions

1. Do not run long-lived services in the foreground from the active Codex shell.
2. Do not directly foreground-run `uvicorn`, `npm run dev`, `next start`,
   `node server.js`, or `math-api.exe`.
3. Do not run `.cmd` files that contain `pause` from automation.
4. Do not launch long-lived services through multi-layer wrappers such as
   `powershell -> cmd.exe -> service` when a direct launcher exists.
5. Do not broadly kill all `node`, `cmd`, `powershell`, `python`, or
   `math-api` processes. Only stop processes whose executable path is inside
   the intended release directory.
6. Do not combine service start, health wait, tests, and stop in one command.
7. Do not retry indefinitely. Retry at most once after a clear cleanup.
8. After an interruption or timeout, inspect and clean residual release
   processes before retrying.

## Bounded Commands

Use `tools/run-bounded.ps1` for non-trivial finite commands:

- unit tests: 180 seconds
- frontend check: 180 seconds
- API call smoke tests: 180 seconds per request
- API generation batch: 900 seconds max
- frontend build: 300 seconds
- API PyInstaller build: 360 seconds
- full release build: 600 seconds
- health checks: 30 seconds total
- service stop: 10 seconds

On timeout, preserve stdout/stderr logs, kill the root process tree, inspect
ports `3000` and `8001`, and do not continue to the next phase automatically.

## Long-Lived Services

Long-lived release services must be managed through:

```text
MathPlatform.exe start
MathPlatform.exe stop
MathPlatform.exe status --json
MathPlatform.exe doctor --json
```

`start` must return quickly and must not wait for readiness. Use `status` or
`doctor` as separate bounded commands for verification.

## Subagents

Use at most 3 subagents, with no nested subagents. Subagents must not start
long-lived services. The main agent owns final merge and verification.
