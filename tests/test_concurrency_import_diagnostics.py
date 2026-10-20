from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from apps.api.app.ai_transactions import run_model_outside_transaction
from apps.api.app.db import Base
from apps.api.app.import_preview import preview_import
from apps.api.app.localization import language_context
from apps.api.app.models import (AnswerSpec, GenerationJob, Question, QuestionRepairRecord,
                                QuestionTag, QuestionVersion, RecoveryAgentRun, SourceFile, TaxonomyTag)
from apps.api.app.routers import admin, recovery_agent
from apps.api.app.schemas import HistoryTransfer, QuestionBankTransfer


def empty_db():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    return Session(engine)


def seeded_bank_db():
    db = empty_db()
    db.add(SourceFile(id='sf-source', filename='source.json', sha256='0' * 64, question_count=1))
    db.add(TaxonomyTag(id='primary:number', kind='primary', label='Numbers', aliases=[], description='Numbers'))
    db.add(Question(id='q-source', source_file_id='sf-source', status='verified', source_name='source.json', stem_preview='Question', difficulty_star=3, premium=False))
    db.add(QuestionVersion(id='v-source', question_id='q-source', version=1, raw_json={'id': 'q-source'}, question_v2={'content': {'stem_md': 'Question'}}, content_hash='a'))
    db.add(QuestionRepairRecord(id='repair-source', question_id='q-source', base_version_id='v-source', status='draft', reason='manual_fix', severity='medium', draft_question_v2={}, base_question_v2={}, diff=[]))
    db.add(AnswerSpec(question_id='q-source', status='verified', answer_type='integer', canonical_answer='1', normalized_spec={}, raw_answer_spec={}, verification={}))
    db.add(QuestionTag(question_id='q-source', tag_id='primary:number', role='level_1', raw_label='Numbers'))
    db.commit()
    return db


def test_remote_wait_does_not_hold_sqlite_writer(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'lock.db'), connect_args={'timeout': 0.1})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SourceFile(id='before-model', filename='before.json', sha256='a', question_count=0))
        db.flush()

        class Chain:
            def run_json(self, *args, **kwargs):
                with Session(engine) as other:
                    other.add(SourceFile(id='during-model', filename='during.json', sha256='b', question_count=0))
                    other.commit()
                return ['provider-result']

        assert run_model_outside_transaction(db, Chain(), [], 'reviewer', {}, {}) == ['provider-result']
        assert db.get(SourceFile, 'during-model') is not None


def test_bank_preview_never_emits_dml_and_maps_incoming_references():
    source = seeded_bank_db()
    payload = admin.export_question_bank(source)
    db = empty_db()
    statements = []

    @event.listens_for(db.bind, 'before_cursor_execute')
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        assert statement.lstrip().split()[0].upper() in {'SELECT', 'PRAGMA'}

    result = admin.import_question_bank(payload, dry_run=True, db=db)
    assert result.status == 'dry_run'
    assert result.imported_counts['questions'] == 1
    assert result.imported_counts['question_repair_records'] == 1
    assert not result.skipped_rows
    assert not db.new and not db.dirty and not db.deleted
    assert len(statements) <= len(admin.QUESTION_BANK_TABLES) + 1
    assert db.get(SourceFile, 'sf-source') is None


def test_preview_detects_conflicting_question_without_mutating():
    source = seeded_bank_db()
    payload = admin.export_question_bank(source)
    db = seeded_bank_db()
    from apps.api.app.models import Question
    db.get(Question, 'q-source').stem_preview = 'Different question'
    db.commit()
    result = admin.import_question_bank(payload, dry_run=True, db=db)
    assert result.imported_counts['questions'] == 0
    assert result.skipped_rows['question_versions'] == 1
    assert any(row['kind'] == 'question_id_content_conflict' for row in result.conflict_report)
    assert db.get(Question, 'q-source').stem_preview == 'Different question'


def test_preview_large_relationship_set_uses_table_scans_not_per_row_queries():
    source = seeded_bank_db()
    payload = admin.export_question_bank(source)
    tag, link = payload.tables['taxonomy_tags'][0], payload.tables['question_tags'][0]
    for index in range(45_000):
        tag_id = f'tag-large-{index}'
        payload.tables['taxonomy_tags'].append({**tag, 'id': tag_id, 'label': tag_id})
        payload.tables['question_tags'].append({**link, 'id': f'link-large-{index}', 'tag_id': tag_id})
    db = empty_db()
    queries = []
    event.listen(db.bind, 'before_cursor_execute', lambda _c, _cur, s, _p, _ctx, _m: queries.append(s))
    started = time.monotonic()
    result = admin.import_question_bank(payload, dry_run=True, db=db)
    assert result.imported_counts['question_tags'] == 45_001
    assert len(queries) <= len(admin.QUESTION_BANK_TABLES) + 1
    assert time.monotonic() - started < 20


def test_actual_import_rejects_active_generation_but_preview_is_allowed():
    source = seeded_bank_db()
    payload = admin.export_question_bank(source)
    db = empty_db()
    db.add(GenerationJob(id='active', status='running', request={}, progress={}))
    db.commit()
    with pytest.raises(HTTPException) as caught:
        admin.import_question_bank(payload, db=db)
    assert caught.value.status_code == 409
    assert caught.value.detail['message_key'] == 'imports.active_tasks'
    assert admin.import_question_bank(payload, dry_run=True, db=db).status == 'dry_run'
    db.get(GenerationJob, 'active').status = 'cancelled'
    db.commit()
    assert admin.import_question_bank(payload, db=db).status == 'imported'


def test_history_preview_is_read_only():
    db = empty_db()
    payload = HistoryTransfer(exported_at=datetime.now().isoformat(), tables={'source_files': [
        {'id': 'source-preview', 'filename': 'preview.json', 'sha256': 'a', 'question_count': 0}
    ]})
    result = admin.import_history(payload, dry_run=True, db=db)
    assert result.imported_counts['source_files'] == 1
    assert db.get(SourceFile, 'source-preview') is None


def test_preview_rejects_dangling_reference():
    source = seeded_bank_db()
    payload = admin.export_question_bank(source)
    payload.tables['question_tags'][0]['tag_id'] = 'missing'
    result = admin.import_question_bank(payload, dry_run=True, db=empty_db())
    assert result.skipped_rows['question_tags'] == 1
    assert any(row['kind'] == 'missing_reference' for row in result.conflict_report)


def run_record(run_id, language, *, actions=None, created=None):
    return RecoveryAgentRun(id=run_id, status='completed', model='gpt-5.6-sol', max_loops=1, loop_count=1,
        input_snapshot={} if language is None else {'output_language': language},
        steps=[], report={'diagnosis': 'saved report', 'actions_taken': actions or []},
        created_at=created or datetime(2026, 10, 17), completed_at=datetime(2026, 10, 17, 12))


def test_diagnosis_complete_does_not_claim_repaired():
    row = run_record('observe', 'en', actions=[{'action': 'observe_only', 'ok': True}])
    result = recovery_agent._serialize_run(row)
    assert result['diagnosis_status'] == 'completed_no_repairs'
    assert result['repair_action_count'] == 0
    assert result['output_language'] == 'en'
    assert result['completed_at'].endswith('+00:00')
    row.report = {'actions_taken': [{'action': 'recover_stale_state', 'ok': True}]}
    result = recovery_agent._serialize_run(row)
    assert result['diagnosis_status'] == 'completed_actions_unverified'
    assert result['repair_action_count'] == 1


def test_diagnosis_history_prefers_interface_language_without_translation():
    db = empty_db()
    old = datetime(2026, 10, 17)
    db.add_all([run_record('english', 'en', created=old), run_record('chinese', 'zh-CN', created=old + timedelta(hours=1))])
    db.commit()
    before = db.get(RecoveryAgentRun, 'chinese').report.copy()
    result = recovery_agent.list_recovery_agent_runs('en', 50, db)
    assert result['runs'][0]['id'] == 'english'
    assert 'input_snapshot' not in result['runs'][0]
    assert 'steps' not in result['runs'][0]
    assert db.get(RecoveryAgentRun, 'chinese').report == before


def test_unknown_historical_language_stays_unknown():
    assert recovery_agent._serialize_run(run_record('legacy', None))['output_language'] is None


@pytest.mark.parametrize('language,expected', [('en', 'No issue'), ('zh-CN', '没有发现')])
def test_deterministic_diagnosis_respects_output_language(language, expected):
    with language_context(language):
        result = recovery_agent._deterministic_decision({'task_center': {'tasks': []}})
    assert result['diagnosis'].startswith(expected)


def test_real_export_preview_on_read_only_isolated_database():
    root = Path(__file__).resolve().parents[1]
    transfer = root / '.tmp/language-acceptance-20261017/question-bank-transfer-private.json'
    path = root / 'dist/math-platform-language-candidate-20261017-r2/data/math_platform_dev.db'
    if not transfer.exists() or not path.exists():
        pytest.skip('Private isolated acceptance fixture is not distributed.')
    payload = QuestionBankTransfer.model_validate(json.loads(transfer.read_text(encoding='utf-8')))
    engine = create_engine('sqlite+pysqlite:///file:' + path.as_posix() + '?mode=ro&uri=true')
    with Session(engine) as db:
        started = time.monotonic()
        result = admin.import_question_bank(payload, dry_run=True, db=db)
        elapsed = time.monotonic() - started
    assert result.imported_counts['questions'] == payload.table_counts['questions']
    assert result.imported_counts['question_tags'] == payload.table_counts['question_tags']
    assert elapsed < 20
    print(f'PRIVATE_BANK_PREVIEW rows={sum(payload.table_counts.values())} seconds={elapsed:.3f}')
