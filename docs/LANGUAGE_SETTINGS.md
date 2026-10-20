# Interface and AI output languages

The default interface is English. The language selector is available globally.
AI output defaults to **Follow interface**; select English or Simplified Chinese
independently when you want, for example, an English UI and Chinese practice.

- Switching languages does not generate or translate content and does not make a paid AI request.
- Questions, reports and user writing already saved keep their original language.
- A queued generation task keeps the language selected when submitted.
- Assistant action confirmations use the saved draft language, not a later browser selection.
- AI questions without a known language are not reused as if they matched a selected language.
- A missing translation for an active prompt version blocks that AI operation instead of silently using another version.

## API contract

`X-Math-Platform-Language` selects the UI/service language.
`X-Math-Platform-Output-Language` selects the AI output language.
An explicit JSON `output_language` overrides the output header, then the UI header,
then the default English. Unsupported explicit output languages are rejected.
`GET /api/settings/languages` exposes supported languages and resource availability.

## Adding a language

1. Add its metadata to `packages/i18n/languages.json`.
2. Add every semantic message key to `packages/i18n/locales/<code>/`.
3. Supply the corresponding prompt versions in `prompts/<code>/`; preserve JSON contracts, security rules and difficulty standards.
4. Run `node tools/check-language-resources.cjs` and `node tools/build-language-resources.cjs`.
5. Run offline tests, independent TypeScript checks and the new language's real-provider acceptance matrix before enabling it in a release.

The registry is extensible. Some legacy domain-title parsers and inline prompt
instructions still require migration; adding locale files alone is not yet proof
that a third language is fully supported. Only English and Simplified Chinese are
officially enabled.

## 中文说明

界面默认英文，可在全局语言栏切换中文。AI 输出默认跟随界面，也可以单独选择。
切换语言不会触发收费调用，不翻译旧题、旧报告或用户输入。排队任务及助手确认动作
使用提交时保存的语言。旧 AI 题语言未标注时，不会冒充所选语言参与复用。
缺少当前启用 prompt 版本的对应语言文件时，相关 AI 操作会明确失败。

验收结果和未完成项目见 `LANGUAGE_ACCEPTANCE_REPORT.md`，不以构建成功代替功能通过。
