# API 清单

错误响应统一保留原 `detail`，并增加：

```json
{
  "error_code": "STATE_VERSION_CONFLICT",
  "user_message": "页面状态已经过期，系统没有覆盖较新的作答状态。",
  "action": "刷新当前 session 状态后再继续。",
  "retryable": true,
  "retry_after_seconds": null,
  "trace_id": "...",
  "details_safe": {}
}
```

前端会优先显示 `user_message`、`error_code`、`action` 和 `trace_id`，不直接展示堆栈、密钥或完整内部 payload。

- `GET /api/health`: 健康检查。
- `POST /api/import/json-files`: 幂等导入 6 份内置 JSON，包括原始 5 份富化题库和 GPT6 整理 AI 题源。
- `GET /api/questions`: 按题源、状态、星级、精品和标签筛选题库。
- `GET /api/questions/{id}`: 返回题目版本、答案规范、标签和来源。
- `POST /api/practice/sessions`: 创建练习，只抽 `verified` 题。
- `POST /api/practice/sessions/{id}/submit`: 提交答案并写入判分结果。请求可带 `idempotency_key` 和 `expected_state_version`；重复 key 返回首次结果，版本冲突返回 `409 state_version_conflict`；结果包含 `evaluation_snapshot_v2`，记录题目版本和答案规范 hash，但不暴露标准答案明文。
- `POST /api/practice/sessions/{id}/judge-answer`: 普通练习下手动请求 AI 答案复核。请求可带 `idempotency_key` 和 `expected_state_version`，避免重复模型调用和旧页面覆盖状态；响应包含同口径 `evaluation_snapshot_v2`。
- `POST /api/practice/sessions/{id}/questions/{question_id}/reveal-answer`: 普通练习下看答案并锁题。请求可带 `idempotency_key` 和 `expected_state_version`；首次成功后递增题目状态版本，后续提交返回 `409 answer_locked_after_reveal`。
- `POST /api/practice/sessions/{id}/question-support`: 提交题目格式/题意质疑；如确认题目有问题，替换同星级题。
- `POST /api/practice/sessions/{id}/questions/{question_id}/mastery-check`: 普通练习下预生成一题同星级、同核心能力的掌握检测题；生成完成后追加到当前 session，用户手动开始。
- `POST /api/generation/jobs`: 创建 AI 出题任务，默认同步跑 mock 流水线。
- `GET /api/generation/jobs/{id}`: 查看候选和审核结果。
- `GET /api/generation/jobs/{id}/events`: SSE 返回任务、候选题和 AI 调用日志；事件包含 `id:`，浏览器自动重连时可用 `Last-Event-ID` 回放缺失事件。
- `GET /api/generation/batches/{id}/events`: SSE 返回套题批次进度；事件包含 `batch_item`、`batch`、`done`、`heartbeat`、`timeout`，支持 `Last-Event-ID` 续传和前端去重。
- `POST /api/generation/batches/{id}/cancel`: 取消套题批次；无在途 item 时直接 `cancelled`，有在途 item 时进入 `cancelling`，停止派发新题并阻止 late result 进入练习队列。
- `GET /api/reports/summary`: 基础正确率、题源、星级和标签统计。
- `GET /api/settings/models`: 返回 OpenAI-compatible 模型配置，不返回 API Key。
- `PUT /api/settings/models`: 更新模型配置；API Key 写入后端加密配置，不返回前端明文。Windows 默认使用 `data\config_encryption_key.dpapi` 保护配置加密密钥；旧 `CONFIG_ENCRYPTION_KEY` 继续兼容。
- `GET /api/admin/history/export`: 导出本地答题、报告、生成题、AI 日志和自定义标签记录，不包含 API Key。
- `POST /api/admin/history/import`: 导入上述历史记录，按源文件名+hash 安全映射 `source_file_id`；支持 `?dry_run=true` 校验不写库。
- `GET /api/admin/question-bank/export`: 导出题库包，包含题目、版本、答案规范、标签和审核队列，不包含作答历史和 API Key。
- `POST /api/admin/question-bank/import`: 导入题库包，按源文件名+hash 安全映射来源；支持 `?dry_run=true` 校验不写库；同名不同 hash、同题 ID 内容冲突、同题版本 hash 冲突、同题答案规范冲突都会写入 `conflict_report`，冲突题及其子记录会跳过。
- `GET /api/admin/diagnostics/export`: 导出平台内脱敏诊断包，包含配置摘要、SQLite 完整性、关键表计数、失败 job/batch、最近脱敏审计日志、最近 AI 调用摘要和磁盘空间；不包含 API Key、密文、完整 provider 请求响应、完整学生自由文本或完整题面。
- `POST /api/admin/destructive-challenges`: 为清空历史、停用题目生成一次性动态确认码；响应包含 `target_count` 和 `target_fingerprint`，执行危险操作时会重新计算影响范围，变化则返回 409。
- `POST /api/admin/questions/delete`: 软停用题目，标记 retired 并排除练习/严格测试，不删除历史证据。
