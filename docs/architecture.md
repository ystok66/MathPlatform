# 数学出题与学情诊断平台架构

## Scope

第一阶段实现 P0+P1 的本地单用户纵向切片：题库导入、统一答案规范、练习判分、作答记录、基础报告、OpenAI-compatible/LiaoBots 出题流水线。

## Components

- `apps/api`: FastAPI + SQLAlchemy + Alembic。负责导入、查询、判分、练习、报告、AI 网关。
- `apps/web`: Next.js + React。负责题库、练习、报告、出题工坊和模型设置界面。
- `workers/ai`: Redis 队列 worker。生产环境可异步消费 `generation_jobs`。
- `packages/schemas`: QuestionV2 和 AI 候选输出 JSON schema。
- `prompts`: planner/generator/reviewer/reporter 等提示词版本。
- `scripts/import`: 幂等导入 6 份内置 JSON，并可整理 `GPT出题6份` 生成 AI 题源导入包。
- `scripts/backup`: PostgreSQL 与关键配置备份恢复脚本。

## Data Flow

1. `POST /api/import/json-files` 读取 `做题项目` 下 6 份 JSON。
2. 原始整题对象写入 `question_versions.raw_json`。
3. 平台映射写入 `question_versions.question_v2`。
4. 富化答案规范写入 `answer_specs.raw_answer_spec` 和 `answer_specs.normalized_spec`。
5. 练习只从 `verified` 且 `answer_specs.status='verified'` 的题目抽题。
6. 每次提交写入 `submissions` 和 `answer_events`。
7. AI 出题保存 `generation_jobs`、`generated_candidates`、`ai_run_logs`，接受题生成不可变版本。

## Difficulty Policy

华罗庚杯和丘班题源按原始 `difficulty_quality_evaluation.difficulty_star` 使用，不做默认降权。报告中保留题源和星级分布，后续用真实作答数据观察偏难影响。
