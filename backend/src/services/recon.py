"""双源核对单：固定两源点位、逐源接收、差值与缺失裁定、封单生成可调查批次。"""

import hashlib
import json
import math
import threading
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Dataset, User
from src.models.recon import (ReconAdoption, ReconDifference, ReconPoint, ReconReceipt,
                              ReconRun, ReconSheet)
from src.services.audit import record
from src.utils.serialization import checksum

EPS = 1e-9
SOURCE_LABELS = {'primary': '主记录器', 'backup': '备记录器'}
DECISIONS = ('primary', 'backup', 'exclude')

# 同一核对单的写操作在进程内串行；数据库行锁负责跨进程串行。
_locks = {}
_locks_guard = threading.Lock()
_open_lock = threading.RLock()


def _lock_for(sheet_id):
    with _locks_guard:
        return _locks.setdefault(sheet_id, threading.RLock())


def _same(a, b):
    return a is not None and b is not None and abs(a - b) <= EPS


def _number(value, field):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, f'{field}必须为数值')
    if not math.isfinite(number):
        raise HTTPException(422, f'{field}必须为有限数值')
    return number


def _definition(sheet):
    return {'name': sheet['name'], 'primary_label': sheet['primary_label'],
            'backup_label': sheet['backup_label'], 'points': sheet['points']}


def _open_key(definition):
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _receipt(db, action, request_id):
    if not request_id:
        return None
    return db.scalar(select(ReconReceipt).where(ReconReceipt.action == action,
                                               ReconReceipt.request_id == request_id))


def _save_receipt(db, sheet_id, action, request_id, payload):
    if request_id:
        db.add(ReconReceipt(sheet_id=sheet_id, action=action, request_id=request_id,
                            payload=payload, created_at=time.time()))


def _commit_receipt(db, sheet_id, action, request_id, payload):
    """落库并留存回执。并发撞上同一操作编号时返回先到的回执，不重复落账。"""
    _save_receipt(db, sheet_id, action, request_id, payload)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if request_id:
            found = _receipt(db, action, request_id)
            if found and found.sheet_id == sheet_id:
                return found.payload
        raise HTTPException(409, '同一操作正在被并发处理，请刷新后重试')
    return payload


def _replayed(db, action, request_id, sheet_id):
    found = _receipt(db, action, request_id)
    if not found:
        return None
    if found.sheet_id != sheet_id:
        raise HTTPException(409, '该操作编号已用于其他核对单，请更换后重试')
    return found.payload


def _sheet_or_404(db, sheet_id, for_update=False):
    query = select(ReconSheet).where(ReconSheet.id == sheet_id)
    if for_update:
        query = query.with_for_update()
    sheet = db.scalar(query)
    if not sheet:
        raise HTTPException(404, '双源核对单不存在')
    return sheet


def _points(db, sheet_id):
    return list(db.scalars(select(ReconPoint).where(ReconPoint.sheet_id == sheet_id)
                           .order_by(ReconPoint.seq)))


def _status_for(primary, backup):
    if primary is None or backup is None:
        return 'waiting'
    return 'matched' if _same(primary, backup) else 'conflict'


def _summary(points):
    counts = {'waiting': 0, 'matched': 0, 'conflict': 0, 'decided': 0}
    for point in points:
        counts[point.status] = counts.get(point.status, 0) + 1
    excluded = sum(1 for point in points if point.status == 'decided'
                   and point.decision == 'exclude')
    adopted = sum(1 for point in points if point.adopted_value is not None)
    return {'total': len(points), 'waiting': counts['waiting'], 'matched': counts['matched'],
            'conflict': counts['conflict'], 'decided': counts['decided'],
            'excluded': excluded, 'adopted': adopted,
            'pending': counts['waiting'] + counts['conflict']}


def _point_dict(point, users):
    return {'id': point.id, 'seq': point.seq, 'sample': point.sample, 'route': point.route,
            'minute': point.minute, 'primary_key': point.primary_key,
            'backup_key': point.backup_key, 'primary_value': point.primary_value,
            'backup_value': point.backup_value, 'primary_at': point.primary_at,
            'backup_at': point.backup_at, 'status': point.status,
            'decision': point.decision, 'adopted_value': point.adopted_value,
            'note': point.note, 'decided_at': point.decided_at,
            'decided_by': users.get(point.decided_by) if point.decided_by else None}


def _difference_dict(diff):
    return {'id': diff.id, 'point_id': diff.point_id, 'source': diff.source,
            'sample': diff.sample, 'value': diff.value, 'kind': diff.kind,
            'adopted_value': diff.adopted_value, 'note': diff.note,
            'request_id': diff.request_id, 'at': diff.at}


def _adoption_dict(row, users):
    return {'id': row.id, 'point_id': row.point_id, 'decision': row.decision,
            'adopted_value': row.adopted_value, 'primary_value': row.primary_value,
            'backup_value': row.backup_value, 'note': row.note,
            'revision': row.revision, 'decided_at': row.decided_at,
            'decided_by': users.get(row.decided_by) if row.decided_by else None}


def _run_dict(run):
    return {'id': run.id, 'revision': run.revision, 'dataset_id': run.dataset_id,
            'status': run.status, 'detail': run.detail,
            'created_at': run.created_at, 'finished_at': run.finished_at}


def _users(db):
    return {user.id: {'id': user.id, 'name': user.name, 'email': user.email,
                      'role': user.role} for user in db.scalars(select(User))}


def detail(db, sheet):
    points = _points(db, sheet.id)
    users = _users(db)
    differences = list(db.scalars(select(ReconDifference)
                                  .where(ReconDifference.sheet_id == sheet.id)
                                  .order_by(ReconDifference.id)))
    adoptions = list(db.scalars(select(ReconAdoption)
                                .where(ReconAdoption.sheet_id == sheet.id)
                                .order_by(ReconAdoption.id.desc())))
    runs = list(db.scalars(select(ReconRun).where(ReconRun.sheet_id == sheet.id)
                           .order_by(ReconRun.revision.desc())))
    summary = _summary(points)
    batch = None
    if sheet.batch_dataset_id:
        dataset = db.get(Dataset, sheet.batch_dataset_id)
        if dataset:
            batch = {'dataset_id': dataset.id, 'name': dataset.name, 'source': dataset.source,
                     'rows': len(dataset.records), 'checksum': dataset.checksum}
    pending = [point.sample for point in points if point.status in ('waiting', 'conflict')]
    return {'id': sheet.id, 'name': sheet.name, 'note': sheet.note,
            'primary_label': sheet.primary_label, 'backup_label': sheet.backup_label,
            'status': sheet.status, 'revision': sheet.revision,
            'point_count': sheet.point_count, 'definition_checksum': sheet.definition_checksum,
            'created_at': sheet.created_at, 'sealed_at': sheet.sealed_at,
            'batch_dataset_id': sheet.batch_dataset_id, 'batch': batch,
            'summary': summary, 'pending_samples': pending[:20],
            'can_seal': sheet.status == 'open' and summary['pending'] == 0,
            'points': [_point_dict(point, users) for point in points],
            'differences': [_difference_dict(diff) for diff in differences],
            'adoptions': [_adoption_dict(row, users) for row in adoptions],
            'runs': [_run_dict(run) for run in runs]}


def list_sheets(db):
    sheets = list(db.scalars(select(ReconSheet).order_by(ReconSheet.id.desc())))
    result = []
    for sheet in sheets:
        points = _points(db, sheet.id)
        summary = _summary(points)
        batch = db.get(Dataset, sheet.batch_dataset_id) if sheet.batch_dataset_id else None
        result.append({'id': sheet.id, 'name': sheet.name, 'note': sheet.note,
                       'primary_label': sheet.primary_label,
                       'backup_label': sheet.backup_label, 'status': sheet.status,
                       'revision': sheet.revision, 'point_count': sheet.point_count,
                       'definition_checksum': sheet.definition_checksum,
                       'created_at': sheet.created_at, 'sealed_at': sheet.sealed_at,
                       'summary': summary,
                       'pending_samples': [p.sample for p in points
                                           if p.status in ('waiting', 'conflict')][:20],
                       'batch': {'dataset_id': batch.id, 'name': batch.name,
                                 'rows': len(batch.records), 'checksum': batch.checksum}
                       if batch else None})
    return result


def open_sheet(db, data, user, request_id=None):
    """开单：一次性固定两源预期点位、键与对应关系。同一请求或同一定义重试不新增。"""
    with _open_lock:
        if request_id:
            found = _receipt(db, 'open', request_id)
            if found:
                sheet = db.get(ReconSheet, found.sheet_id)
                if sheet:
                    return detail(db, sheet), False
        name = data.name.strip()
        if len(name) < 2:
            raise HTTPException(422, '核对单名称去除空格后至少需要两个字符')
        primary_label = data.primary_label.strip() or '主记录器'
        backup_label = data.backup_label.strip() or '备记录器'
        if primary_label == backup_label:
            raise HTTPException(422, '两源名称不能相同')
        seen_samples, seen_primary, seen_backup = set(), set(), set()
        points = []
        for index, raw in enumerate(data.points):
            sample = raw.sample.strip()
            route = raw.route.strip()
            primary_key = raw.primary_key.strip()
            backup_key = raw.backup_key.strip()
            if not sample or not route or not primary_key or not backup_key:
                raise HTTPException(422, f'第 {index + 1} 个点位须填写采样点、路线和两源来源键')
            if sample in seen_samples:
                raise HTTPException(409, f'第 {index + 1} 个点位采样点 {sample} 重复')
            if primary_key in seen_primary:
                raise HTTPException(409, f'第 {index + 1} 个点位{primary_label}键 {primary_key} 重复')
            if backup_key in seen_backup:
                raise HTTPException(409, f'第 {index + 1} 个点位{backup_label}键 {backup_key} 重复')
            seen_samples.add(sample)
            seen_primary.add(primary_key)
            seen_backup.add(backup_key)
            points.append({'sample': sample, 'route': route,
                           'minute': _number(raw.minute, f'第 {index + 1} 个点位发车后分钟'),
                           'primary_key': primary_key, 'backup_key': backup_key})
        definition = {'name': name, 'primary_label': primary_label,
                      'backup_label': backup_label, 'points': points}
        key = _open_key(definition)
        existing = db.scalar(select(ReconSheet).where(ReconSheet.open_key == key))
        if existing:
            return detail(db, existing), False
        if db.scalar(select(ReconSheet).where(ReconSheet.name == name)):
            raise HTTPException(409, '该核对单名称已存在，请修改名称或沿用原单')
        sheet = ReconSheet(name=name, note=data.note.strip(), primary_label=primary_label,
                           backup_label=backup_label, status='open', revision=0,
                           point_count=len(points), definition_checksum=key, open_key=key,
                           created_by=user.id, created_at=time.time())
        db.add(sheet)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(ReconSheet).where(ReconSheet.open_key == key))
            if existing:
                return detail(db, existing), False
            raise HTTPException(409, '该核对单名称或点位定义已存在，请修改后重试')
        for index, point in enumerate(points, start=1):
            db.add(ReconPoint(sheet_id=sheet.id, seq=index, status='waiting', **point))
        record(db, user.email, 'recon.open', {'id': sheet.id, 'points': len(points),
                                              'checksum': key})
        payload = detail(db, sheet)
        return _commit_receipt(db, sheet.id, 'open', request_id, payload), True


def intake(db, sheet_id, data, user, request_id=None):
    """逐源接收读数。接收后不改写已收原值；封单后整条接收只记差异。"""
    source = data.source
    if source not in SOURCE_LABELS:
        raise HTTPException(422, '读数来源只接受 primary 或 backup')
    seen = set()
    readings = []
    for index, item in enumerate(data.readings):
        key = item.key.strip()
        if not key:
            raise HTTPException(422, f'第 {index + 1} 条读数缺少来源键')
        if key in seen:
            raise HTTPException(409, f'本次接收中来源键 {key} 重复')
        seen.add(key)
        readings.append((key, _number(item.value, f'第 {index + 1} 条读数')))
    with _lock_for(sheet_id):
        sheet = _sheet_or_404(db, sheet_id, for_update=True)
        if request_id:
            payload = _replayed(db, 'readings', request_id, sheet_id)
            if payload:
                return payload
        points = _points(db, sheet_id)
        by_key = {point.primary_key if source == 'primary' else point.backup_key: point
                  for point in points}
        # 曾出现同源重复读数不一致的点位，即使两源随后看似一致也须人工裁定。
        disputed = set(db.scalars(select(ReconDifference.point_id)
                                  .where(ReconDifference.sheet_id == sheet_id,
                                         ReconDifference.kind == 'repeat_mismatch',
                                         ReconDifference.point_id.is_not(None))))
        received = duplicates = conflicts = unexpected = post_sealed = post_decided = 0
        samples = []
        for key, value in readings:
            point = by_key.get(key)
            if not point:
                db.add(ReconDifference(sheet_id=sheet.id, point_id=None, source=source,
                                       sample=key, value=value, kind='unexpected_key',
                                       note=data.note.strip(), request_id=request_id,
                                       at=time.time()))
                unexpected += 1
                continue
            current = point.primary_value if source == 'primary' else point.backup_value
            if _same(current, value):
                # 相同读数只用一次：无论是否已裁定/封单都不重复计数，也不记差异。
                duplicates += 1
                continue
            if sheet.status == 'sealed':
                db.add(ReconDifference(sheet_id=sheet.id, point_id=point.id, source=source,
                                       sample=point.sample, value=value, kind='post_seal',
                                       adopted_value=point.adopted_value,
                                       note=data.note.strip(), request_id=request_id,
                                       at=time.time()))
                post_sealed += 1
                continue
            if point.status == 'decided':
                db.add(ReconDifference(sheet_id=sheet.id, point_id=point.id, source=source,
                                       sample=point.sample, value=value, kind='post_decision',
                                       adopted_value=point.adopted_value,
                                       note=data.note.strip(), request_id=request_id,
                                       at=time.time()))
                post_decided += 1
                continue
            if current is None:
                if source == 'primary':
                    point.primary_value, point.primary_at = value, time.time()
                else:
                    point.backup_value, point.backup_at = value, time.time()
                received += 1
                samples.append(point.sample)
            else:
                db.add(ReconDifference(sheet_id=sheet.id, point_id=point.id, source=source,
                                       sample=point.sample, value=value, kind='repeat_mismatch',
                                       adopted_value=point.adopted_value,
                                       note=data.note.strip(), request_id=request_id,
                                       at=time.time()))
                disputed.add(point.id)
                if point.status == 'matched':
                    point.adopted_value = None
                    conflicts += 1
                continue
            if point.id in disputed:
                point.status = 'conflict'
                point.adopted_value = None
            else:
                point.status = _status_for(point.primary_value, point.backup_value)
            if point.status == 'matched':
                point.adopted_value = point.primary_value
                db.add(ReconAdoption(sheet_id=sheet.id, point_id=point.id, decision='same',
                                     adopted_value=point.primary_value,
                                     primary_value=point.primary_value,
                                     backup_value=point.backup_value, note='两源读数一致',
                                     revision=sheet.revision, decided_by=user.id,
                                     decided_at=time.time()))
        record(db, user.email, 'recon.readings',
               {'id': sheet.id, 'source': source, 'received': received,
                'duplicates': duplicates, 'unexpected': unexpected,
                'post_seal': post_sealed, 'post_decision': post_decided})
        db.flush()
        points = _points(db, sheet.id)
        payload = {'sheet_id': sheet.id, 'source': source, 'received': received,
                   'duplicates': duplicates, 'conflicts': conflicts,
                   'unexpected': unexpected, 'post_seal': post_sealed,
                   'post_decision': post_decided, 'samples': samples[:20],
                   'summary': _summary(points), 'sheet_status': sheet.status}
        return _commit_receipt(db, sheet.id, 'readings', request_id, payload)


def _apply_decision(db, sheet, point, data, user, revision):
    decision = data.decision
    if decision not in DECISIONS:
        raise HTTPException(422, '裁定只接受采用主值、采用备值或排除该点')
    note = data.note.strip()
    if decision == 'primary' and point.primary_value is None:
        raise HTTPException(409, '该点位尚无主记录器原值，不能采用主值')
    if decision == 'backup' and point.backup_value is None:
        raise HTTPException(409, '该点位尚无备记录器原值，不能采用备值')
    adopted = point.primary_value if decision == 'primary' else (
        point.backup_value if decision == 'backup' else None)
    if decision == 'exclude' and not note:
        raise HTTPException(422, '排除点位须填写裁定依据')
    point.decision = decision
    point.adopted_value = adopted
    point.status = 'decided'
    point.note = note
    point.decided_by = user.id
    point.decided_at = time.time()
    db.add(ReconAdoption(sheet_id=sheet.id, point_id=point.id, decision=decision,
                         adopted_value=adopted, primary_value=point.primary_value,
                         backup_value=point.backup_value, note=note, revision=revision,
                         decided_by=user.id, decided_at=point.decided_at))


def _mark_superseded(db, sheet_id, revision):
    for run in db.scalars(select(ReconRun).where(ReconRun.sheet_id == sheet_id,
                                                 ReconRun.status == 'running')):
        if run.revision < revision:
            run.status = 'superseded'
            run.detail = '已有更新的裁定，旧执行不再写入'
            run.finished_at = time.time()


def decide(db, sheet_id, point_id, data, user, request_id=None):
    with _lock_for(sheet_id):
        sheet = _sheet_or_404(db, sheet_id, for_update=True)
        if request_id:
            payload = _replayed(db, 'decide', request_id, sheet_id)
            if payload:
                return payload
        point = db.scalar(select(ReconPoint).where(ReconPoint.id == point_id,
                                                   ReconPoint.sheet_id == sheet_id))
        if not point:
            raise HTTPException(404, '核对点位不存在')
        if data.decision not in DECISIONS:
            raise HTTPException(422, '裁定只接受采用主值、采用备值或排除该点')
        note = data.note.strip()
        wanted = point.primary_value if data.decision == 'primary' else (
            point.backup_value if data.decision == 'backup' else None)
        if data.decision == 'primary' and point.primary_value is None:
            raise HTTPException(409, '该点位尚无主记录器原值，不能采用主值')
        if data.decision == 'backup' and point.backup_value is None:
            raise HTTPException(409, '该点位尚无备记录器原值，不能采用备值')
        if data.decision == 'exclude' and not note:
            raise HTTPException(422, '排除点位须填写裁定依据')
        reused = (point.status == 'decided' and point.decision == data.decision
                  and point.note == note and _same(point.adopted_value, wanted))
        if reused:
            # 同一裁定重试：不新增修订，只补做可能中断的批次生成。
            if sheet.status == 'sealed':
                payload = _generate_batch(db, sheet_id, sheet.revision)
                return _commit_receipt(db, sheet_id, 'decide', request_id, payload)
            payload = detail(db, sheet)
            db.commit()
            return _commit_receipt(db, sheet_id, 'decide', request_id, payload)
        if sheet.status == 'sealed':
            if user.role != 'admin':
                raise HTTPException(403, '封单后的重新裁定需要业务负责人确认')
            sheet.revision += 1
            revision = sheet.revision
            _mark_superseded(db, sheet_id, revision)
            run = ReconRun(sheet_id=sheet_id, revision=revision, status='running',
                           created_at=time.time())
            db.add(run)
            action = 'recon.redecide'
        else:
            revision = sheet.revision
            run = None
            action = 'recon.decide'
        _apply_decision(db, sheet, point, data, user, revision)
        record(db, user.email, action, {'id': sheet_id, 'point': point_id,
                                        'decision': data.decision, 'revision': revision})
        db.flush()
        if run is not None:
            # 裁定先落库，再生成新批次；中途中断可由重试恢复。
            db.commit()
            payload = _generate_batch(db, sheet_id, revision)
        else:
            payload = detail(db, sheet)
            db.commit()
        return _commit_receipt(db, sheet_id, 'decide', request_id, payload)


def _build_records(points):
    records = []
    for point in points:
        if point.status not in ('matched', 'decided'):
            raise HTTPException(409, f'点位 {point.sample} 尚无结论，暂不能生成可调查批次')
        if point.adopted_value is None:
            continue
        records.append({'sample': point.sample, 'route': point.route,
                        'minute': point.minute, 'temperature': point.adopted_value})
    return records


def _generate_batch(db, sheet_id, revision):
    """按修订号幂等生成可调查批次；旧修订执行不得覆盖新裁定。"""
    sheet = db.scalar(select(ReconSheet).where(ReconSheet.id == sheet_id).with_for_update())
    if not sheet:
        raise HTTPException(404, '双源核对单不存在')
    run = db.scalar(select(ReconRun).where(ReconRun.sheet_id == sheet_id,
                                           ReconRun.revision == revision))
    if run and run.status == 'superseded':
        return detail(db, sheet)
    if run and run.status == 'completed' and run.dataset_id:
        return detail(db, sheet)
    if revision != sheet.revision:
        if run and run.status == 'running':
            run.status = 'superseded'
            run.detail = '已有更新的裁定，旧执行不再写入'
            run.finished_at = time.time()
            db.commit()
        return detail(db, sheet)
    points = _points(db, sheet_id)
    records = _build_records(points)
    if not records:
        raise HTTPException(409, '所有点位均无可用采用值，不能生成可调查批次')
    name = f'{sheet.name} · 可调查批次 R{revision}'
    existing = db.scalar(select(Dataset).where(Dataset.name == name))
    digest = checksum(records)
    if existing:
        if existing.checksum != digest:
            raise HTTPException(409, '同名批次内容不一致，请核对裁定后再试')
        dataset = existing
    else:
        dataset = Dataset(name=name, source=f'双源核对单 #{sheet.id} · R{revision}',
                          records=records, checksum=digest, created_at=time.time())
        db.add(dataset)
        db.flush()
    if run is None:
        run = ReconRun(sheet_id=sheet_id, revision=revision, status='running',
                       created_at=time.time())
        db.add(run)
    run.dataset_id = dataset.id
    run.status = 'completed'
    run.detail = f'采用 {len(records)} 个点位'
    run.finished_at = time.time()
    if sheet.revision == revision:
        sheet.batch_dataset_id = dataset.id
    record(db, 'system', 'recon.batch', {'id': sheet.id, 'revision': revision,
                                         'dataset': dataset.id, 'rows': len(records)})
    db.commit()
    return detail(db, sheet)


def seal(db, sheet_id, data, user, request_id=None):
    """封单：全部点位有结论才生成可调查批次。重复调用返回原单与原批次。"""
    with _lock_for(sheet_id):
        sheet = _sheet_or_404(db, sheet_id, for_update=True)
        if request_id:
            payload = _replayed(db, 'seal', request_id, sheet_id)
            if payload:
                return payload
        points = _points(db, sheet_id)
        summary = _summary(points)
        if sheet.status == 'sealed':
            revision = sheet.revision
            run = db.scalar(select(ReconRun).where(ReconRun.sheet_id == sheet_id,
                                                   ReconRun.revision == revision))
            incomplete = run is None or run.status != 'completed' or not run.dataset_id
            if incomplete:
                if run is None:
                    run = ReconRun(sheet_id=sheet_id, revision=revision, status='running',
                                   created_at=time.time())
                    db.add(run)
                db.commit()
                payload = _generate_batch(db, sheet_id, revision)
            else:
                payload = detail(db, sheet)
                db.commit()
            return _commit_receipt(db, sheet_id, 'seal', request_id, payload)
        if summary['pending']:
            pending = [point.sample for point in points
                       if point.status in ('waiting', 'conflict')][:12]
            raise HTTPException(409, '尚有 {0} 个点位缺少读数或存在异值，须全部裁定后才能封单：{1}'
                                .format(summary['pending'], '、'.join(pending)))
        sheet.status = 'sealed'
        sheet.sealed_at = time.time()
        sheet.revision += 1
        revision = sheet.revision
        run = ReconRun(sheet_id=sheet_id, revision=revision, status='running',
                       created_at=time.time())
        db.add(run)
        record(db, user.email, 'recon.seal', {'id': sheet_id, 'revision': revision,
                                              'note': data.note.strip()})
        db.flush()
        # 封单与采用值先提交，批次生成失败时重试可恢复同一原单。
        db.commit()
        payload = _generate_batch(db, sheet_id, revision)
        return _commit_receipt(db, sheet_id, 'seal', request_id, payload)


def point_trace(db, sheet_id, point_id):
    sheet = _sheet_or_404(db, sheet_id)
    point = db.scalar(select(ReconPoint).where(ReconPoint.id == point_id,
                                               ReconPoint.sheet_id == sheet_id))
    if not point:
        raise HTTPException(404, '核对点位不存在')
    users = _users(db)
    differences = list(db.scalars(select(ReconDifference)
                                  .where(ReconDifference.sheet_id == sheet_id,
                                         ReconDifference.point_id == point_id)
                                  .order_by(ReconDifference.id)))
    adoptions = list(db.scalars(select(ReconAdoption)
                                .where(ReconAdoption.sheet_id == sheet_id,
                                       ReconAdoption.point_id == point_id)
                                .order_by(ReconAdoption.id.desc())))
    return {'sheet': {'id': sheet.id, 'name': sheet.name, 'status': sheet.status,
                      'revision': sheet.revision,
                      'primary_label': sheet.primary_label,
                      'backup_label': sheet.backup_label,
                      'batch_dataset_id': sheet.batch_dataset_id},
            'point': _point_dict(point, users),
            'differences': [_difference_dict(diff) for diff in differences],
            'adoptions': [_adoption_dict(row, users) for row in adoptions]}
