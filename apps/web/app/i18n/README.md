# Frontend language support

## Contract

- English (en) is the default. Alternate: zh-CN. Global React context with English / 中文 selector; localStorage key math-platform-language-v1. Storage failures retain session switching.
- Existing state and module-level UI constants remain canonical; render-time catalog/template lookup makes switching reactive without resetting practice or forms. Only document lang/title use metadata effects. No DOM text scanning or MutationObserver.
- Shared API, readiness fetch, and fetch-based SSE send X-Math-Platform-Language. The proxy defaults missing/invalid language to en. No API-body enrichment or external translator.
- SSE retains event IDs on language reconnect; the existing polling fallback handles stream errors.

## Coverage and intentional limits

- Static controls, guide text, local errors, empty states, dialogs, and UI templates are cataloged across practice, generated sets, history, bank, reports, guide, admin, task center, and assistant controls.
- Source/tag keys, IDs, enum/status values, option values, JSON editor/export payloads, names, question stems/answers, learner input, conversation content, and report prose are not rewritten. Stored Chinese bank text therefore remains Chinese in English mode.
- Server-returned status/task labels and errors translate only when matching a known catalog entry at an explicit UI sink. Unknown server prose remains unchanged; backend localization is required. Existing response snapshots may retain their original language until normal refresh/poll. Switching does not replay writes or paid AI calls.
- Async assistant responses and server fallback text remain authored content, including responses from requests started before switching. Controls and local status messages are translated; no automatic resubmission or external translation occurs.
- The untouched built-in generation constraint follows the selected UI language, including the existing constraints request field. Editing the field or applying copied/report/assistant constraints opts out; supplied text is preserved even if it equals a default. Other pedagogical request text, saved user drafts, and imported labels remain data. No language fields are injected into request bodies.
- Legacy interpolated UI messages use catalog templates; only explicitly UI-derived parameters are recursively localized, not arbitrary user/source/question values.
- Compound legacy strings and server-only messages may require additional catalog entries. No browser-level all-string coverage claim is made.

## Delivery evidence

- One bounded TypeScript check is authorized after application; see the chat result for its actual exit status.
- No build, services, browser smoke, paid API calls, rendered-layout review, or end-to-end localization audit was run by this frontend worker.
- tools/i18n-localize.cjs is the scoped AST migration and skips already-localized source. The catalog is explicit, not an automatic translation service.

## Packaged-smoke omission repair

- Added explicit backend-authored metric definitions/exclusions, deterministic review-plan text, and task title/progress templates in system-catalog.ts.
- Task-center title translation is limited to known built-in titles for the target type. Custom titles in available batch metadata are preserved. If a custom title exactly equals a built-in title and its batch metadata is unavailable, the task summary does not expose enough provenance to distinguish them.
- Task descriptions translate only recognized batch/intervention/recovery templates. IDs, model names, raw stage keys, custom topics, history titles, and unknown descriptions remain unchanged.
- Heatmap title/aria-label now use explicit language-aware interpolation, preserving the original tag label. Generation eyebrow and revisit schedule use reactive translation.
- Main owns repackaging and repeat browser smoke. A passing TypeScript check is not evidence of a passing packaged UI smoke.
