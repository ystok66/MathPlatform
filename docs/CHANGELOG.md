# CHANGELOG

本文件记录面向使用者和维护者的可读变更摘要。可验证的版本、依赖、迁移、导入导出 schema 和发布边界，以 `docs/generated/compatibility_matrix.*`、`docs/generated/release_checklist.*` 和 `release_manifest.json` 为准。

## [Unreleased] - 2026-07-23

### Added

- 新增维护者事实源生成链路：OpenAPI、数据字典/ER 图、状态机、错误码、Prompt 契约、备份恢复 runbook、数据库迁移指南、发布 checklist、安全威胁模型、ADR 索引和兼容性矩阵。
- 新增 ADR 模板和“维护者事实源从代码和 Schema 生成”架构决策记录。
- 新增安全边界与威胁模型，覆盖浏览器写请求、API Key、题面渲染、Prompt 输入、严格测试、危险管理操作和诊断/备份边界。

### Changed

- 项目总说明、审查文档和 checklist 状态文档改为引用自动生成事实源，降低手工文档与实现漂移风险。
- 发布验证继续遵守 Windows bounded 命令规则，长期服务启动、检查和停止分阶段执行。

### Compatibility

- 发布包继续以 Windows 本地单用户为目标，API/Web 绑定 `127.0.0.1`。
- 发布包 SQLite 运行时继续通过 `ensure_sqlite_dev_schema()` 做兼容列补齐；非 SQLite 数据库仍以 Alembic revision 链为 schema 管理来源。
- 历史迁移包仍使用 `history_transfer_v1`，题库迁移包仍使用 `question_bank_transfer_v1`。
