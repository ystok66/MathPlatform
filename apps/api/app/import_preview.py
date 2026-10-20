"""Read-only, bounded transfer preview. No ORM objects are added or merged."""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
from time import monotonic
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from .localization import translate_message
from .models import (AssistantConversation, GenerationBatch, GenerationJob,
                     InterventionRecord, RecoveryAgentRun, SystemTask)

_IMPORT_WRITE_LOCK = Lock()
MAX_PREVIEW_ROWS = 200_000
PREVIEW_SECONDS = 20


def _error(status: int, key: str):
    return HTTPException(status_code=status, detail={
        'message_key': key, 'message_params': {}, 'message': translate_message(key),
    })


def acquire_import_guard(db):
    """Serialize imports in this process and reject known active AI work."""
    if not _IMPORT_WRITE_LOCK.acquire(blocking=False):
        raise _error(409, 'imports.busy')
    try:
        active = ('queued', 'running', 'cancelling', 'generating')
        for model in (GenerationBatch, GenerationJob, RecoveryAgentRun, SystemTask, AssistantConversation):
            if hasattr(model, 'status') and db.scalar(select(model.id).where(model.status.in_(active)).limit(1)):
                raise _error(409, 'imports.active_tasks')
        if db.scalar(select(InterventionRecord.id).where(InterventionRecord.prefetch_status == 'generating').limit(1)):
            raise _error(409, 'imports.active_tasks')
        return _IMPORT_WRITE_LOCK
    except BaseException:
        _IMPORT_WRITE_LOCK.release()
        raise


def preview_import(db, payload, models, *, question_bank: bool):
    # Reuse coercion and domain conflict rules, but never use the write helpers.
    from .routers import admin

    total = sum(len(rows) for name, rows in payload.tables.items() if name in models)
    if total > MAX_PREVIEW_ROWS:
        raise _error(413, 'imports.preview_too_large')
    deadline = monotonic() + PREVIEW_SECONDS

    def check_time():
        if monotonic() > deadline:
            raise _error(408, 'imports.preview_timeout')

    indexes = {}
    # Avoid loading large question JSON, raw AI responses or student free text.
    needed = {
        'source_files': ('id', 'filename', 'sha256'),
        'questions': ('id', 'source_file_id', 'stem_preview', 'exact_hash', 'normalized_hash'),
        'question_versions': ('id', 'question_id', 'version', 'content_hash'),
        'answer_specs': ('id', 'question_id', 'answer_type', 'canonical_answer', 'raw_answer_spec'),
        'question_tags': ('id', 'question_id', 'tag_id'),
        'question_duplicate_aliases': ('id', 'alias_question_id', 'canonical_question_id', 'status'),
        'review_queue_items': ('id', 'question_id', 'reason_code'),
    }
    with db.no_autoflush:
        for name, model in models.items():
            check_time()
            columns = [model.__table__.c[key] for key in needed.get(name, ('id',))]
            indexes[name] = {str(row['id']): dict(row) for row in db.execute(select(*columns)).mappings()}

    source_map, version_map = {}, {}
    counts, skipped = {}, defaultdict(int)
    conflicts = []
    skipped_questions = set()
    source_by_name = {row.get('filename'): row for row in indexes.get('source_files', {}).values()}
    versions = {(row.get('question_id'), row.get('version')): row for row in indexes.get('question_versions', {}).values()}
    answers = {row.get('question_id'): row for row in indexes.get('answer_specs', {}).values()}
    question_tags = {(row.get('question_id'), row.get('tag_id')): row for row in indexes.get('question_tags', {}).values()}
    aliases = {row.get('alias_question_id'): row for row in indexes.get('question_duplicate_aliases', {}).values() if row.get('status') == 'active'}

    def conflict(name, row, kind):
        skipped[name] += 1
        if len(conflicts) < 200:
            conflicts.append({'table': name, 'kind': kind, 'id': str(row.get('id') or '')})

    def exists(table, key):
        return key is not None and str(key) in indexes.get(table, {})

    # Order follows the existing transfer contract. Incoming rows are staged in
    # Python indexes so later foreign keys can refer to earlier incoming rows.
    for name, model in models.items():
        if name not in payload.tables:
            continue
        counts[name] = 0
        for number, raw in enumerate(payload.tables[name]):
            if number % 256 == 0:
                check_time()
            if not isinstance(raw, dict) or not raw.get('id'):
                skipped[name] += 1
                continue
            try:
                row = admin.coerce_import_row(model, raw, source_map)
            except (ValueError, TypeError, OverflowError):
                conflict(name, raw, 'invalid_field_value')
                continue
            if name == 'source_files':
                filename = str(row.get('filename') or '')
                if not filename:
                    conflict(name, row, 'missing_filename')
                    continue
                old_id = str(row['id'])
                target = source_by_name.get(filename)
                if target and admin.source_file_conflict(SimpleNamespace(**target), str(row.get('sha256') or '')):
                    filename = admin.source_conflict_filename(filename, str(row.get('sha256') or ''))
                    target = source_by_name.get(filename)
                    if not target:
                        target = {**row, 'id': str(uuid5(NAMESPACE_URL, filename)), 'filename': filename}
                    if len(conflicts) < 200:
                        conflicts.append({'table': name, 'kind': 'filename_hash_conflict', 'filename': row['filename'],
                                          'mapped_filename': filename, 'mapped_source_file_id': target['id'], 'preview_only': True})
                if target:
                    row = {**row, 'id': target['id'], 'filename': filename}
                source_map[old_id] = str(row['id'])
                source_by_name[filename] = row
            if question_bank and admin.row_touches_skipped_question(model, row, skipped_questions):
                skipped[name] += 1
                continue
            if question_bank and name == 'questions':
                existing = indexes[name].get(str(row['id']))
                if existing and admin.question_row_conflicts(SimpleNamespace(**existing), row):
                    skipped_questions.add(str(row['id']))
                    conflict(name, row, 'question_id_content_conflict')
                    continue
            if question_bank and name == 'question_versions':
                existing_id = indexes[name].get(str(row['id']))
                existing = versions.get((row.get('question_id'), row.get('version')))
                if (existing_id and existing_id.get('question_id') != row.get('question_id')) or any(
                    item and item.get('content_hash') and row.get('content_hash') and item['content_hash'] != row['content_hash']
                    for item in (existing_id, existing)
                ):
                    conflict(name, row, 'question_version_content_conflict')
                    continue
                if existing:
                    version_map[str(row['id'])] = existing['id']
                    row['id'] = existing['id']
            if question_bank and name == 'question_repair_records':
                for key in ('base_version_id', 'new_version_id'):
                    row[key] = version_map.get(str(row.get(key)), row.get(key))
            if question_bank and name == 'answer_specs':
                existing = answers.get(row.get('question_id'))
                if existing and admin.answer_spec_row_conflicts(SimpleNamespace(**existing), row):
                    conflict(name, row, 'answer_spec_content_conflict')
                    continue
                if existing:
                    row['id'] = existing['id']
            if question_bank and name == 'question_tags':
                existing = question_tags.get((row.get('question_id'), row.get('tag_id')))
                if existing:
                    row['id'] = existing['id']
            if question_bank and name == 'question_duplicate_aliases':
                existing = aliases.get(row.get('alias_question_id'))
                if existing and existing.get('canonical_question_id') != row.get('canonical_question_id'):
                    conflict(name, row, 'duplicate_alias_canonical_conflict')
                    continue
                if existing:
                    row['id'] = existing['id']
            missing_required = any(
                not column.nullable and not column.primary_key and column.default is None and column.server_default is None
                and row.get(column.name) is None for column in model.__table__.columns
            )
            if missing_required:
                conflict(name, row, 'missing_required_field')
                continue
            missing_fk = False
            for column in model.__table__.columns:
                value = row.get(column.name)
                if value is None:
                    continue
                for fk in column.foreign_keys:
                    target_name = fk.column.table.name
                    # The supported transfer graph uses ID foreign keys.
                    if target_name in indexes and not exists(target_name, value):
                        missing_fk = True
            if name == 'question_variants' and (not exists('questions', row.get('base_question_id')) or not exists('questions', row.get('variant_question_id'))):
                missing_fk = True
            if missing_fk:
                conflict(name, row, 'missing_reference')
                continue
            indexes[name][str(row['id'])] = row
            if name == 'question_versions':
                versions[(row.get('question_id'), row.get('version'))] = row
            elif name == 'answer_specs':
                answers[row.get('question_id')] = row
            elif name == 'question_tags':
                question_tags[(row.get('question_id'), row.get('tag_id'))] = row
            elif name == 'question_duplicate_aliases' and row.get('status') == 'active':
                aliases[row.get('alias_question_id')] = row
            counts[name] += 1
    check_time()
    return {'status': 'dry_run', 'dry_run': True, 'imported_counts': counts,
            'skipped_tables': [name for name in models if name not in payload.tables],
            'skipped_rows': dict(skipped), 'conflict_report': conflicts}
