# Shared Schemas

`question_v2.schema.json` is the platform mapping target for imported and generated questions.
`ai_candidate.schema.json` is the P1 generator output contract.
`status_catalog.ts` is generated from `apps/api/app/statuses.py` and gives the frontend a typed view of backend status domains, status values, severities, and allowed transitions.

Regenerate status types after editing backend statuses:

```powershell
python scripts\dev\generate_status_types.py
python scripts\dev\generate_status_types.py --check
```

The full historical schema lives in `做题项目/数学出题与学情诊断平台_完整方案包/数学出题与学情诊断平台_方案包/02_question_v2.schema.json`.
