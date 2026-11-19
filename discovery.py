"""Persistent, bounded searches within an explicit user-provided story family."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import time

import runtime as app
from families import Catalog, Family, canonical_event, digest, integer
from streaming import Queue, atomic_text, job_lock, process_event, safe_text, stop_producer


def seconds(text):
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(s|m|h)?', str(text))
    if not match:
        raise ValueError('Бюджет должен выглядеть как 30s, 10m или 8h')
    value = float(match[1]) * {None: 1, 's': 1, 'm': 60, 'h': 3600}[match[2]]
    if not 0 < value <= 30 * 24 * 3600:
        raise ValueError('Бюджет должен быть положительным и не более 30 дней')
    return value


def backend_key(catalog):
    from standard_reasoning import VERSION as reasoning_version
    build = app.ROOT / 'BUILD.json'
    return digest({'catalog': catalog.data, 'build': build.read_text() if build.exists() else '', 'reasoning': reasoning_version, 'quality': 1,
                   'modules': {name: digest((app.ROOT / name).read_text()) for name in ('families.py', 'discovery.py', 'standard_reasoning.py', 'streaming.py', 'quality.py') if (app.ROOT / name).exists()}})


class CampaignQueue(Queue):
    def __init__(self, job, catalog):
        super().__init__(job)
        self.catalog = catalog
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS search_jobs (
            id TEXT PRIMARY KEY, plan TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'planned',
            pid INTEGER, started REAL, finished REAL, elapsed REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS candidate_keys (fingerprint TEXT PRIMARY KEY, owner TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS origins (
            job TEXT NOT NULL, local_id TEXT NOT NULL, candidate TEXT NOT NULL, owner TEXT NOT NULL,
            PRIMARY KEY(job,local_id));
        ''')
        self.db.commit()

    def import_events(self, source):
        added = 0
        identifier = source.name
        known = {r[0] for r in self.db.execute('SELECT local_id FROM origins WHERE job=?', (identifier,))}
        for path in sorted((source / 'events').glob('*.json')):
            local_id = path.stem
            if local_id in known:
                continue
            global_id = identifier + '__' + local_id
            state, message, owner, fingerprint = 'queued', '', global_id, None
            event = {'theorem': path.name, 'sourceJob': identifier}
            try:
                if not re.fullmatch(r'[A-Za-z0-9_-]+', local_id):
                    raise ValueError('Invalid event name')
                event = json.loads(path.read_text())
                if not isinstance(event, dict) or event.get('schemaVersion') != 1 or event.get('id') != local_id:
                    raise ValueError('Invalid event schema/id')
                if any(not isinstance(event.get(k), str) or not event[k].strip() for k in ('configuration', 'theorem', 'readable')):
                    raise ValueError('Missing event fields')
                event.update(id=global_id, sourceJob=identifier, sourceEventId=local_id)
                try:
                    fingerprint = canonical_event(event, self.catalog)
                    event['deduplication'] = 'structural'
                except (ValueError, KeyError, TypeError, IndexError):
                    # Unknown notation is never geometrically merged on a guess.
                    fingerprint = digest([event['configuration'], event['theorem']])
                    event['deduplication'] = 'exact-text'
                previous = self.db.execute('SELECT owner FROM candidate_keys WHERE fingerprint=?', (fingerprint,)).fetchone()
                if previous:
                    owner = previous['owner']; state = 'duplicate'; message = 'Повтор задачи ' + owner
            except (OSError, ValueError, TypeError) as error:
                state, message = 'error', str(error)
                event = {'id': global_id, 'theorem': path.name, 'sourceJob': identifier}
            atomic_text(self.job / 'events' / (global_id + '.json'), json.dumps(event, ensure_ascii=False))
            with self.db:
                self.db.execute('INSERT INTO tasks(id,event,fingerprint,state,message,discovered) VALUES(?,?,?,?,?,?)',
                                (global_id, json.dumps(event, ensure_ascii=False), fingerprint, state, message, time.time()))
                if fingerprint and owner == global_id:
                    self.db.execute('INSERT INTO candidate_keys VALUES(?,?)', (fingerprint, global_id))
                self.db.execute('INSERT INTO origins VALUES(?,?,?,?)', (identifier, local_id, global_id, owner))
            added += 1
        return added

    def next_search(self):
        rows = list(self.db.execute("SELECT * FROM search_jobs WHERE state='planned' ORDER BY id"))
        if not rows:
            return None
        completed = self.db.execute("SELECT count(*) FROM search_jobs WHERE state!='planned'").fetchone()[0]
        # Deterministic exploration slots keep low-yield families alive.
        if completed % 4 == 0:
            return rows[0]
        def direction(plan):
            return (plan['source'], tuple(sorted(plan['palette'])), plan['relaxedFrom'], plan.get('extensionFamily'))
        statistics = {}
        for job in self.db.execute("SELECT id,plan,elapsed FROM search_jobs WHERE state IN ('completed','limited','failed','interrupted','invalid')"):
            source = direction(json.loads(job['plan']))
            stats = statistics.setdefault(source, [0, 0, 0])
            ready = self.db.execute("SELECT COALESCE(sum(COALESCE(q.score,100))/100.0,0) FROM tasks t LEFT JOIN quality_reports q ON q.id=t.id WHERE t.state='ready' AND t.id LIKE ?", (job['id'] + '__%',)).fetchone()[0]
            stats[0] += ready; stats[1] += job['elapsed']; stats[2] += 1
        def score(row):
            source = direction(json.loads(row['plan']))
            accepted, elapsed, attempts = statistics.get(source, [0, 0, 0])
            # Optimism for untried sources; reward only post-check unique ready tasks.
            return accepted / max(1, elapsed) + math.sqrt(math.log(completed + 2) / (attempts + 1))
        return max(rows, key=score)


def create(family_file, output, *, budget, search_workers=2, check_workers=2, check_ms=1000, view=False):
    if isinstance(budget, bool) or not isinstance(budget, (float, int)) or not math.isfinite(budget) or not 0 < budget <= 30 * 86400:
        raise ValueError('Неверный бюджет поиска')
    integer(search_workers, 'search_workers', 1, 32)
    integer(check_workers, 'check_workers', 1, 32)
    integer(check_ms, 'check_ms', 1, 3600000)
    catalog = Catalog.read(app.ROOT / 'data/construction-catalog.json')
    family = Family.read(family_file, catalog)
    plans = family.plans()
    if not plans:
        raise ValueError('Семейство не создало ни одного поискового задания')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for sub in ('events', 'geo', 'tasks', 'searches', 'family'):
        (output / sub).mkdir()
    atomic_text(output / 'family/source-config.json', json.dumps(family.config, ensure_ascii=False, indent=2))
    snapshot_config = dict(family.config, stories=[f'story-{i:04}.txt' for i in range(len(family.stories))])
    atomic_text(output / 'family/config.json', json.dumps(snapshot_config, ensure_ascii=False, indent=2))
    for i, story in enumerate(family.stories):
        atomic_text(output / f'family/story-{i:04}.txt', story.text())
    atomic_text(output / 'family/catalog.json', json.dumps(catalog.data, ensure_ascii=False, indent=2))
    metadata = {'kind': 'campaign', 'version': 1, 'phase': 'created', 'searchState': 'stopped',
                'extraTheorems': [t for t in os.environ.get('GEOGEN_EXTRA_THEOREMS','').split(',') if t],
                'createdAt': time.time(), 'budgetSeconds': budget, 'spentSeconds': 0,
                'searchWorkers': search_workers, 'checkWorkers': check_workers, 'checkMs': check_ms,
                'sliceSeconds': family.config['search']['sliceSeconds'], 'backendKey': backend_key(catalog),
                'qualityChecks': True, 'qualityOptions': dict(samples=family.config['quality']['samples'],milliseconds=family.config['quality']['milliseconds'],triangle_domain=family.config['quality']['triangleDomain']), 'familyName': family.config.get('name', Path(family_file).stem)}
    queue = CampaignQueue(output, catalog)
    try:
        with queue.db:
            for i, plan in enumerate(plans):
                queue.db.execute('INSERT INTO search_jobs(id,plan) VALUES(?,?)',
                                 (f'j{i:06}', json.dumps(plan, ensure_ascii=False)))
        queue.export(metadata)
    finally:
        queue.close()
    return run(output, view=view)


def _recover_searches(queue):
    for row in queue.db.execute("SELECT * FROM search_jobs WHERE state IN ('running','preparing')").fetchall():
        # Never kill a reused PID: verify the isolated job's exact settings argument.
        pid = row['pid']
        try:
            arguments = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            expected = str(queue.job / 'searches' / row['id'] / 'settings.json').encode()
            if expected in arguments:
                os.killpg(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while Path(f'/proc/{pid}/cmdline').exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                if Path(f'/proc/{pid}/cmdline').exists() and expected in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
                    os.killpg(pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
        with queue.db:
            queue.db.execute("UPDATE search_jobs SET state='interrupted',message=? WHERE id=?",
                             ('Поиск был прерван; сохранённые события будут обработаны. DFS с точного места не восстанавливается.', row['id']))
    for source in sorted((queue.job / 'searches').iterdir()):
        queue.import_events(source)


def _start_search_unchecked(queue, row):
    source = queue.job / 'searches' / row['id']
    source.mkdir()
    for sub in ('events', 'inputs'):
        (source / sub).mkdir()
    plan = json.loads(row['plan'])
    atomic_text(source / 'inputs/input_story.txt', plan['story'])
    atomic_text(source / 'origin.json', json.dumps(plan, ensure_ascii=False, indent=2))
    settings = app.job_settings(source)
    settings['ProblemGenerationRunnerSettings']['StreamingOutputFolder'] = str(source / 'events')
    atomic_text(source / 'settings.json', json.dumps(settings, ensure_ascii=False))
    env = os.environ.copy()
    env.update(DOTNET_ROOT=str(app.DOTNET.parent), DOTNET_MULTILEVEL_LOOKUP='0', DOTNET_PROCESSOR_COUNT='1')
    log = (source / 'geogen.log').open('w')
    try:
        producer = subprocess.Popen([str(app.DOTNET), str(app.ROOT / 'lib/geogen/GeoGen.MainLauncher.dll'), str(source / 'settings.json')],
                                    cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()
    try:
        with queue.db:
            queue.db.execute("UPDATE search_jobs SET state='running',pid=?,started=? WHERE id=?", (producer.pid, time.time(), row['id']))
    except BaseException:
        stop_producer(producer)
        raise
    return producer, time.monotonic()


def _start_search(queue,row):
    with queue.db:
        queue.db.execute("UPDATE search_jobs SET state='preparing' WHERE id=?",(row['id'],))
    try:
        return _start_search_unchecked(queue,row)
    except BaseException:
        with queue.db:
            queue.db.execute("UPDATE search_jobs SET state='interrupted' WHERE id=?",(row['id'],))
        raise


def run(output, *, extra_budget=0, search_workers=None, check_workers=None, retry_errors=False, view=False, drain_seconds=30, recheck=False):
    if not isinstance(extra_budget,(int,float)) or isinstance(extra_budget,bool) or not math.isfinite(extra_budget) or extra_budget < 0:
        raise ValueError('Неверный добавочный бюджет')
    if search_workers is not None: integer(search_workers,'search_workers',1,32)
    if check_workers is not None: integer(check_workers,'check_workers',1,32)
    output = Path(output).resolve()
    catalog = Catalog.read(output / 'family/catalog.json')
    with job_lock(output):
        metadata = json.loads((output / 'run-state.json').read_text())
        os.environ['GEOGEN_EXTRA_THEOREMS'] = ','.join(metadata.get('extraTheorems', []))
        if metadata.get('kind') != 'campaign' or metadata.get('version') != 1:
            raise ValueError('Неизвестный формат кампании')
        current_catalog = Catalog.read(app.ROOT / 'data/construction-catalog.json')
        changed_backend = metadata['backendKey'] != backend_key(current_catalog)
        if changed_backend and not recheck:
            raise ValueError('Сборка/проверки изменились. Для сохранения кампании и повторной проверки результатов используйте resume --recheck.')
        if recheck and catalog.data != current_catalog.data:
            raise ValueError('Каталог построений изменился; создайте новую кампанию, чтобы не смешивать разные определения.')
        if extra_budget:
            metadata['budgetSeconds'] += extra_budget
        if search_workers is not None:
            metadata['searchWorkers'] = search_workers
        if check_workers is not None:
            metadata['checkWorkers'] = check_workers
        queue = CampaignQueue(output, catalog)
        _recover_searches(queue)
        if recheck:
            archive = output / 'archive' / (metadata['backendKey'][:12]+'-'+str(time.time_ns()))
            archive.mkdir(parents=True)
            with sqlite3.connect(archive/'tasks.sqlite') as backup:
                queue.db.backup(backup)
            for file in (output/'geo').glob('*.geo'):
                (archive/'geo').mkdir(exist_ok=True)
                file.replace(archive/'geo'/file.name)
            queue.db.execute("UPDATE tasks SET state='queued',message='',geo='',finished=NULL WHERE state!='duplicate' AND json_extract(event,'$.configuration') IS NOT NULL")
            queue.db.execute('DELETE FROM quality_reports')
            queue.db.commit()
            metadata.update(backendKey=backend_key(current_catalog),qualityChecks=True)
        queue.db.execute("UPDATE tasks SET state='queued' WHERE state='processing'")
        if retry_errors:
            queue.db.execute("UPDATE tasks SET state='queued',message='',finished=NULL WHERE state IN ('error','timeout')")
        queue.db.commit()
        metadata.update(phase='searching', searchState='running')
        queue.export(metadata)
        producers = {}; pending = {}; pool = ThreadPoolExecutor(max_workers=metadata['checkWorkers'])
        started = time.monotonic(); spent = metadata['spentSeconds']; interrupted = False
        remaining = max(0, metadata['budgetSeconds'] - spent); search_ended = None
        try:
            if view:
                open_view(output)
            while True:
                now = time.monotonic(); within_budget = now - started < remaining
                changed = False
                for identifier, (producer, began) in list(producers.items()):
                    source = output / 'searches' / identifier
                    changed = bool(queue.import_events(source)) or changed
                    limited = producer.poll() is None and (not within_budget or now - began >= metadata['sliceSeconds'])
                    if limited:
                        stop_producer(producer)
                    if producer.poll() is not None:
                        queue.import_events(source)
                        log = (source / 'geogen.log').read_text(errors='replace')
                        failed = not limited and (producer.returncode or "Couldn't perform the algorithm" in log or '[ERR]' in log or '[FTL]' in log)
                        invalid = failed and 'InitializationException' in log and json.loads(queue.db.execute('SELECT plan FROM search_jobs WHERE id=?',(identifier,)).fetchone()[0])['addedDefinitions'] > 0
                        state = 'limited' if limited else ('invalid' if invalid else ('failed' if failed else 'completed'))
                        message = 'Лимит поиска; полученные события сохранены' if limited else ('Добавленный вариант геометрически недопустим; см. geogen.log' if invalid else ('Ошибка GeoGen; см. geogen.log' if failed else ''))
                        with queue.db:
                            queue.db.execute('UPDATE search_jobs SET state=?,finished=?,elapsed=?,message=? WHERE id=?',
                                             (state, time.time(), now - began, message, identifier))
                        del producers[identifier]; changed = True
                for future, identifier in list(pending.items()):
                    if future.done():
                        try:
                            result = future.result()
                        except Exception as error:
                            result = {'state': 'error', 'message': str(error)}
                        queue.finish(identifier, result, bool(producers))
                        print(f'{identifier}: {result["state"]}', flush=True)
                        del pending[future]; changed = True
                backlog = queue.db.execute("SELECT count(*) FROM tasks WHERE state IN ('queued','processing')").fetchone()[0]
                while within_budget and len(producers) < metadata['searchWorkers'] and backlog < 1000:
                    row = queue.next_search()
                    if row is None:
                        break
                    try:
                        producers[row['id']] = _start_search(queue, row)
                    except (OSError, ValueError) as error:
                        with queue.db:
                            queue.db.execute("UPDATE search_jobs SET state='failed',message=? WHERE id=?", (str(error), row['id']))
                    changed = True
                planned = queue.db.execute("SELECT count(*) FROM search_jobs WHERE state='planned'").fetchone()[0]
                searching = bool(producers) or (within_budget and bool(planned))
                if not searching and search_ended is None:
                    search_ended = time.monotonic()
                can_drain = searching or time.monotonic() - search_ended < drain_seconds
                while can_drain and len(pending) < metadata['checkWorkers']:
                    row = queue.take()
                    if row is None:
                        break
                    pending[pool.submit(process_event, output, row['id'], json.loads(row['event']), True, metadata['checkMs'], True, metadata.get('qualityOptions') or True)] = row['id']
                    changed = True
                metadata.update(spentSeconds=spent + min((search_ended or time.monotonic()) - started, remaining),
                                phase='searching' if searching else 'draining', searchState='running' if searching else 'stopped')
                if changed:
                    metadata['searchCounts'] = {r[0]: r[1] for r in queue.db.execute('SELECT state,count(*) FROM search_jobs GROUP BY state')}
                    queue.export(metadata)
                if not searching and not pending and (not can_drain or queue.db.execute("SELECT count(*) FROM tasks WHERE state='queued'").fetchone()[0] == 0):
                    break
                time.sleep(.1)
        except BaseException:
            interrupted = True
            raise
        finally:
            for identifier, (producer, began) in producers.items():
                stop_producer(producer)
                queue.import_events(output / 'searches' / identifier)
                with queue.db:
                    queue.db.execute("UPDATE search_jobs SET state='interrupted',finished=?,elapsed=? WHERE id=?", (time.time(), time.monotonic() - began, identifier))
            pool.shutdown(wait=True, cancel_futures=True)
            for future, identifier in pending.items():
                if not future.cancelled():
                    try:
                        queue.finish(identifier, future.result(), False)
                    except Exception as error:
                        queue.finish(identifier, {'state': 'error', 'message': str(error)}, False)
            queue.db.execute("UPDATE tasks SET state='queued' WHERE state='processing'"); queue.db.commit()
            planned = queue.db.execute("SELECT count(*) FROM search_jobs WHERE state='planned'").fetchone()[0]
            metadata.update(spentSeconds=spent + min((search_ended or time.monotonic()) - started, remaining),
                            phase='stopped' if interrupted or planned or queue.db.execute("SELECT count(*) FROM tasks WHERE state='queued'").fetchone()[0] else 'finished', searchState='stopped' if planned or interrupted else 'completed',
                            searchCounts={r[0]: r[1] for r in queue.db.execute('SELECT state,count(*) FROM search_jobs GROUP BY state')})
            counts = queue.export(metadata); queue.close()
            print(f'Кампания: {metadata["phase"]}; {counts}; {output}', flush=True)
    return output


def open_view(output):
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('Для --view нужна графическая сессия')
    subprocess.Popen([str(app.VIEWER), '--watch', str(output)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
