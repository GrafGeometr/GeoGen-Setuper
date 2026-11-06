"""Durable local theorem queue. No server or third-party Python packages required."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import time

import runtime as app

TERMINAL = ('ready', 'unchecked', 'rejected', 'error', 'timeout', 'duplicate')


def atomic_text(path, text):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def safe_text(value):
    return str(value).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')


class BusyJobError(RuntimeError):
    pass


@contextmanager
def job_lock(job):
    with (job / 'stream.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BusyJobError('Этот поток уже обрабатывается другим процессом.')
        yield


class Queue:
    def __init__(self, job):
        self.job = Path(job)
        self.db = sqlite3.connect(self.job / 'tasks.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, event TEXT NOT NULL, fingerprint TEXT,
            state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', geo TEXT NOT NULL DEFAULT '',
            discovered REAL NOT NULL, started REAL, finished REAL, producer_alive INTEGER)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS task_fingerprint ON tasks(fingerprint)')
        self.db.commit()
        self.seen = {row['id'] for row in self.db.execute('SELECT id FROM tasks')}

    def ingest(self):
        added = 0
        for path in sorted((self.job / 'events').glob('*.json')):
            identifier = path.stem
            if identifier in self.seen:
                continue
            event = {'theorem': path.name}
            state, detail, fingerprint = 'queued', '', None
            try:
                event = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(event, dict) or event.get('schemaVersion') != 1:
                    raise ValueError('Unknown event schema')
                if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier) or event.get('id') != identifier:
                    raise ValueError('Invalid event identifier')
                for key in ('configuration', 'theorem', 'readable'):
                    if not isinstance(event.get(key), str) or not event[key].strip():
                        raise ValueError('Missing event field: ' + key)
                fingerprint = hashlib.sha256((event['configuration'] + '\0' + event['theorem']).encode()).hexdigest()
                previous = self.db.execute('SELECT id FROM tasks WHERE fingerprint=? AND state!=? LIMIT 1',
                                           (fingerprint, 'duplicate')).fetchone()
                if previous:
                    state, detail = 'duplicate', 'Повтор события ' + previous['id']
            except (ValueError, OSError, TypeError) as error:
                state, detail = 'error', str(error)
                # Even malformed producer output remains visible as an error.
                event = {'theorem': path.name}
            self.db.execute('INSERT INTO tasks(id,event,fingerprint,state,message,discovered) VALUES(?,?,?,?,?,?)',
                            (identifier, json.dumps(event, ensure_ascii=False), fingerprint, state, detail, time.time()))
            self.seen.add(identifier)
            added += 1
        self.db.commit()
        return added

    def take(self):
        row = self.db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE tasks SET state='processing',started=? WHERE id=?", (time.time(), row['id']))
            self.db.commit()
        return row

    def finish(self, identifier, result, producer_alive):
        self.db.execute('UPDATE tasks SET state=?,message=?,geo=?,finished=?,producer_alive=? WHERE id=?',
                        (result['state'], result.get('message', ''), result.get('geo', ''), time.time(), int(producer_alive), identifier))
        self.db.commit()

    def export(self, metadata):
        counts = {r[0]: r[1] for r in self.db.execute('SELECT state,count(*) FROM tasks GROUP BY state')}
        metadata = dict(metadata, counts=counts, updatedAt=time.time())
        atomic_text(self.job / 'run-state.json', json.dumps(metadata, ensure_ascii=False, indent=2))
        rows = self.db.execute('SELECT * FROM tasks ORDER BY rowid ASC')
        lines = []
        for row in rows:
            event = json.loads(row['event'])
            # Preserve the first five fields for older viewers; metadata is appended.
            score = event.get('ranking')
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                score = ''
            topic = str(event.get('theorem', '')).partition(':')[0].strip() or 'Unknown'
            lines.append('\t'.join(safe_text(x) for x in
                         (row['id'], row['state'], event.get('theorem', ''), row['geo'], row['message'], score, topic)))
        atomic_text(self.job / 'results.tsv', '\n'.join(lines) + ('\n' if lines else ''))
        display_phase = metadata['phase']
        if display_phase == 'finished' and metadata.get('searchState') == 'stopped':
            display_phase = 'Очередь обработана; поиск был остановлен'
        atomic_text(self.job / 'stream-status.txt', display_phase + '\n' +
                    ' · '.join(f'{key}: {value}' for key, value in sorted(counts.items())))
        return counts

    def close(self):
        self.db.close()


def refresh_results(job):
    """Upgrade presentation metadata for existing jobs without rerunning any work."""
    job = Path(job).resolve()
    if not (job / 'run-state.json').is_file() or not (job / 'tasks.sqlite').is_file():
        return False
    try:
        with job_lock(job):
            metadata = json.loads((job / 'run-state.json').read_text(encoding='utf-8'))
            queue = Queue(job)
            try:
                queue.export(metadata)
            finally:
                queue.close()
    except BusyJobError:
        # The active consumer owns its manifest; never compete with it for writes.
        return False
    return True


def finding_filename(identifier, event):
    """Readable type/score prefix and stable identity for ties and interrupted work."""
    kind = event['theorem'].partition(':')[0].strip()
    kind = re.sub(r'[^A-Za-z0-9_-]', '_', kind)[:64] or 'Unknown'
    score = event.get('ranking')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        score = 'unrated'
    return f'{kind}_{score}__{identifier}.geo'


def process_event(job, identifier, event, extending=True, check_ms=1000):
    """One isolated process per check: crashes and timeouts never imply acceptance."""
    task_dir = job / 'tasks' / identifier
    task_dir.mkdir(parents=True, exist_ok=True)
    diagnostic = task_dir / 'worker.log'
    try:
        if extending:
            check_file = task_dir / 'check.geo'
            text = event['configuration'].rstrip() + '\n' + event['theorem'] + '\n\n\n\n\n'
            check_file.write_text(''.join('!' + line + '\n' for line in text.splitlines()), encoding='utf-8')
            checked = subprocess.run([str(app.EXTENDING), '--check-file', str(check_file), str(check_ms)],
                                     text=True, capture_output=True, timeout=check_ms / 1000 + 5,
                                     start_new_session=True)
            diagnostic.write_text(checked.stdout + checked.stderr, encoding='utf-8')
            if checked.returncode:
                raise RuntimeError(f'Extending exited with {checked.returncode}; see {diagnostic}')
            response = json.loads(checked.stdout)
            status = response.get('status')
            if status not in ('accepted', 'rejected', 'error', 'timeout'):
                raise RuntimeError('Invalid Extending response')
            if status != 'accepted':
                return {'state': status, 'message': str(response.get('message', ''))}
        readable = task_dir / 'theorem.txt'
        readable.write_text(event['readable'], encoding='utf-8')
        staging = task_dir / 'converted'
        # An interrupted conversion may have left a partial file, never a published one.
        if staging.exists():
            import shutil
            shutil.rmtree(staging)
        staging.mkdir()
        converted = subprocess.run([str(app.JAVA), '-cp', str(app.ROOT / 'lib/converter/*'),
                                    'MainKt', str(readable), str(staging)],
                                   text=True, capture_output=True, timeout=30, start_new_session=True)
        with diagnostic.open('a', encoding='utf-8') as log:
            log.write(converted.stdout + converted.stderr)
        files = list(staging.glob('*.geo'))
        if converted.returncode or len(files) != 1:
            raise RuntimeError(f'Conversion failed ({converted.returncode}); see {diagnostic}')
        filename = finding_filename(identifier, event)
        files[0].replace(job / 'geo' / filename)
        return {'state': 'ready' if extending else 'unchecked', 'geo': filename}
    except subprocess.TimeoutExpired as error:
        diagnostic.write_text(str(error), encoding='utf-8')
        return {'state': 'timeout', 'message': str(error)}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        with diagnostic.open('a', encoding='utf-8') as log:
            log.write('\n' + str(error))
        return {'state': 'error', 'message': str(error)}


def stop_producer(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def consume(job, metadata, *, producer=None, workers=2, extending=True, check_ms=1000, timeout=3600):
    queue = Queue(job)
    queue.db.execute("UPDATE tasks SET state='queued' WHERE state='processing'")
    queue.db.commit()
    pending = {}
    pool = ThreadPoolExecutor(max_workers=workers)
    started = time.monotonic()
    interrupted = False
    producer_problem = metadata.get("message") if metadata.get("phase") == "failed" else None
    observed_end = producer is None
    prior_search = metadata.get('searchState', 'stopped' if metadata.get('phase') == 'stopped' else 'completed')
    metadata['searchState'] = 'running' if producer else ('stopped' if prior_search == 'running' else prior_search)
    metadata['phase'] = 'searching' if producer else 'draining'
    queue.export(metadata)
    try:
        while True:
            alive = producer is not None and producer.poll() is None
            if alive and time.monotonic() - started > timeout:
                stop_producer(producer)
                alive = False
                producer_problem = f'GeoGen exceeded {timeout}s search limit; remaining queue is preserved.'
            changed = bool(queue.ingest())
            if not alive and not observed_end:
                observed_end = True
                metadata['producerFinishedAt'] = time.time()
                metadata['phase'] = 'draining'
                log = (job / 'geogen.log').read_text(errors='replace')
                if producer.returncode or "Couldn't perform the algorithm" in log or '[ERR]' in log or '[FTL]' in log:
                    producer_problem = producer_problem or 'GeoGen failed; see geogen.log. Received findings are preserved.'
                metadata['searchState'] = 'failed' if producer_problem else 'completed'
                changed = True
            for future, identifier in list(pending.items()):
                if future.done():
                    try:
                        result = future.result()
                    except Exception as error:
                        result = {'state': 'error', 'message': str(error)}
                    queue.finish(identifier, result, producer is not None and producer.poll() is None)
                    print(f'{identifier}: {result["state"]}', flush=True)
                    del pending[future]
                    changed = True
            while len(pending) < workers:
                row = queue.take()
                if not row:
                    break
                future = pool.submit(process_event, job, row['id'], json.loads(row['event']), extending, check_ms)
                pending[future] = row['id']
                changed = True
            if changed:
                queue.export(metadata)
            if not alive and not pending:
                # After exit the producer cannot rename another complete event into view.
                if queue.ingest():
                    continue
                break
            time.sleep(.1)
    except BaseException:
        interrupted = True
        raise
    finally:
        stop_producer(producer)
        if interrupted and producer is not None and not observed_end:
            metadata['searchState'] = 'stopped'
        # Active isolated subprocesses have bounded lifetimes. Preserve their completed
        # results before releasing the single-consumer lock; unstarted rows stay queued.
        pool.shutdown(wait=True, cancel_futures=True)
        for future, identifier in pending.items():
            if not future.cancelled():
                try:
                    queue.finish(identifier, future.result(), False)
                except Exception as error:
                    queue.finish(identifier, {'state': 'error', 'message': str(error)}, False)
        queue.db.execute("UPDATE tasks SET state='queued' WHERE state='processing'")
        queue.db.commit()
        metadata['producerFinishedAt'] = metadata.get('producerFinishedAt', time.time())
        metadata['phase'] = 'stopped' if interrupted else ('failed' if producer_problem else 'finished')
        metadata['message'] = producer_problem or ''
        counts = queue.export(metadata)
        queue.close()
        print(f'Поток: {metadata["phase"]}; {counts}; каталог: {job}', flush=True)
    if producer_problem:
        raise RuntimeError(producer_problem)
    return job


def run_stream(input_file, job, *, workers=2, extending=True, check_ms=1000, timeout=3600, view=False):
    input_file, job = Path(input_file).resolve(), Path(job).resolve()
    if not input_file.is_file():
        raise RuntimeError(f'Нет исходного сюжета: {input_file}')
    if view and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('Для --view нужна графическая сессия; поток можно запустить без --view.')
    job.mkdir(parents=True, exist_ok=False)
    import shutil
    for name in ('inputs', 'events', 'geo', 'tasks'):
        (job / name).mkdir()
    shutil.copyfile(input_file, job / 'inputs/input_story.txt')
    settings = app.job_settings(job)
    settings['ProblemGenerationRunnerSettings']['StreamingOutputFolder'] = str(job / 'events')
    config = job / 'settings.json'
    atomic_text(config, json.dumps(settings, ensure_ascii=False, indent=2))
    env = os.environ.copy()
    env['DOTNET_ROOT'] = str(app.DOTNET.parent)
    env['DOTNET_MULTILEVEL_LOOKUP'] = '0'
    env['DOTNET_PROCESSOR_COUNT'] = str(max(1, workers))
    metadata = {'version': 1, 'createdAt': time.time(), 'extending': extending, 'checkMs': check_ms, 'searchState': 'running'}
    with job_lock(job), (job / 'geogen.log').open('w') as log:
        producer = subprocess.Popen([str(app.DOTNET), str(app.ROOT / 'lib/geogen/GeoGen.MainLauncher.dll'), str(config)],
                                    cwd=job, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        metadata['producerPid'] = producer.pid
        atomic_text(job / 'run-state.json', json.dumps(dict(metadata, phase='searching'), ensure_ascii=False))
        try:
            if view:
                if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
                    raise RuntimeError('Для --view нужна графическая сессия; поток можно запустить без --view.')
                subprocess.Popen([str(app.VIEWER), '--watch', str(job)], env=env, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return consume(job, metadata, producer=producer, workers=workers, extending=extending,
                           check_ms=check_ms, timeout=timeout)
        finally:
            stop_producer(producer)


def resume(job, *, workers=2, retry_errors=False):
    job = Path(job).resolve()
    if not (job / 'run-state.json').is_file():
        raise RuntimeError('Нет run-state.json: это не каталог потокового запуска.')
    with job_lock(job):
        metadata = json.loads((job / 'run-state.json').read_text())
        if metadata['phase'] == 'searching':
            try:
                os.kill(metadata['producerPid'], 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError('Процесс GeoGen ещё существует. Остановите исходный запуск перед resume.')
        if retry_errors:
            queue = Queue(job)
            queue.db.execute("UPDATE tasks SET state='queued',message='',finished=NULL WHERE state IN ('error','timeout')")
            queue.db.commit()
            queue.close()
        return consume(job, metadata, workers=workers, extending=metadata['extending'], check_ms=metadata['checkMs'])
