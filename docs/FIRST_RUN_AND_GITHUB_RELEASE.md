# First-run API setup and public distribution

## User flow

The dialog opens when the local API and model-settings read are ready and there
is no saved key. It does not guess key state while the API is starting. It can
be skipped for bank-only use and reopened through **API setup** in the header.
It follows the English/Chinese interface selection, including its own language
buttons while the modal is open. Strict-focus tests do not open this dialog.

1. Enter an OpenAI-compatible API base URL and API key.
2. Fetch `/models`; filter and select a returned model instead of typing its ID.
3. Test the selected model with one short `/chat/completions` request.
4. Save locally only after a nonempty model reply succeeds.

Changing URL, key or selected model invalidates the previous successful test.
A model list is only the provider's advertised list, not proof every model is
usable. The short call may be billed. An empty provider reply is not success.
The wizard does not send an existing saved key to a changed server address;
enter a new key for that address. It never stores key drafts in browser storage.
Cancel stops waiting and discards late UI results, but cannot guarantee a remote
provider cancels or refunds a request already sent. Saving is not interruptible
from the dialog; an ambiguous timeout asks the user to recheck saved state.

For a new setup, **Use this model for all AI roles and star levels** is selected.
This replaces role and star overrides, sets the strict-reviewer allowlist to
this model and clears old pricing. It does not waive independent-review rules;
users can choose separate reviewer models and prices later in Admin.
Reconfiguring an existing installation preserves role overrides by default.

## Files

- `apps/web/app/components/ApiSetupDialog.tsx`: bounded setup state and modal.
- `apps/web/app/components/ApiSetupDialog.module.css`: responsive modal styles.
- `apps/web/app/page.tsx`: readiness integration and global reopen button.
- `apps/api/app/routers/settings.py`: model discovery, short test and persistence.
- `scripts/pack/prepare_github_release.py`: isolated source/runtime preparation.

## Public preparation

The new folder has `repository/` for Git source and `assets/` for the complete
Windows distribution. The original working release is not modified. Source is
copied from allowlisted locations without dependencies, build output, personal
environment files, logs or used/private databases. Runtime dependencies come from the new
API/web builds and existing launcher/Node runtime. The published environment
starts with an empty key. Question-bank rows go into a newly created SQLite
file, rather than deleting private rows from a copied database with free pages.
No encrypted configuration, learner profiles, answer history, reports, assistant
conversations, generation jobs, provider logs or audit records are copied.

Only question-bank tables listed in the preparation script are retained.
Question metadata is sanitized; private generation provenance is not exported.
Source question/answer content stays in its original language. Original JSON
import dumps are not bundled, preventing retired test questions being reimported.
The two rating/tagging guides remain available to runtime prompts.

## Evidence and remaining work

- This change implements the setup flow and creates a separate distribution.
- Build command results are reported separately; a build is not a runtime test.
- Browser interaction, restart persistence, mock-provider scenarios and paid
  provider calls are NOT_RUN unless separately requested.
- Provider-specific behavior such as restricted `/models`, non-chat model IDs,
  reasoning-token requirements and network interruption can cause a test failure.
- No GitHub repository has been created or uploaded by the preparation script.
- No software license is invented. Competition-question redistribution rights
  must be confirmed by the maintainer before making the bank-containing asset public.

## 中文使用说明

首次启动后，等待本地服务就绪，界面会提示填写 API URL 和 API Key。
点击“连接并获取模型列表”，搜索并选择模型，点击“测试实际模型调用”。
成功后点击“保存并开始”；也可先跳过，不使用 AI 功能。
后续点击页面顶部“API 设置”重新配置，各角色模型仍可在管理页分别设置。

发布时将 repository 的内容作为 GitHub 源码，将 assets 中的 ZIP 作为
Release 附件。新电脑需要解压整个 math-platform 文件夹，再双击
StartMathPlatform.exe 或 START_HERE.cmd，而不是只复制一个 EXE。

## Finalisation update (2026-10-17)

The detailed public README is maintained in `docs/PUBLIC_README.md` and copied
to both public source and runtime roots. `docs/PUBLIC_RELEASE_VALIDATION.md`
records the completed first-run UI/mock/real-call smoke and the remaining work.
The generic PEM exclusion now preserves only certifi's public CA bundle.
No TLS verification is disabled. The finalizer adds a deliberately sanitized
question-bank gzip to source for native Mac builds; it is not a used private DB.
Mac arm64/x86_64 workflow files are supplied, but native builds/tests remain
NOT_RUN on this Windows host. No GitHub upload has been performed.
