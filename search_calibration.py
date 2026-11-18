"""Offline orchestration of original C# searches; no geometry or scoring callbacks in Python."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time

from families import Catalog, Story, digest

FEATURE = 'original-soft-search-v1'


def shuffled_story(story, first_operation, seed):
    rng = random.Random(seed)
    operations = [name for name in story.constructions if name != first_operation]
    rng.shuffle(operations)
    # Only change the order of independent definitions, preserving every definition.
    pending = list(story.definitions)
    defined = set(story.names)
    ordered = []
    while pending:
        ready = [item for item in pending if set(item[2]) <= defined]
        if not ready:
            raise ValueError('Initial definitions are not acyclic')
        item = rng.choice(ready)
        pending.remove(item)
        ordered.append(item)
        defined.add(item[0])
    return replace(story, constructions=(first_operation, *operations), definitions=tuple(ordered))


def distribution(histograms):
    """Each nonempty stratum gets equal mass, regardless of its speed or sample count."""
    mass = defaultdict(float)
    observations = 0
    strata = 0
    for histogram in histograms:
        total = sum(count for _, count in histogram)
        if total <= 0:
            continue
        strata += 1
        observations += int(total)
        for value, count in histogram:
            mass[float(value)] += count / total
    if not mass:
        return {'samples': 0, 'strata': 0}
    values = sorted(mass.items())
    def quantile(fraction):
        target = fraction * strata
        cumulative = 0.
        for value, weight in values:
            cumulative += weight
            if cumulative + 1e-12 >= target:
                return value
        return values[-1][0]
    return dict(samples=observations, strata=strata, minimum=values[0][0], maximum=values[-1][0],
                p10=quantile(.10), p25=quantile(.25), median=quantile(.5), p75=quantile(.75), p90=quantile(.90))


def summarize(records):
    families = []
    for source in sorted({row['source'] for row in records}):
        rows = [row for row in records if row['source'] == source]
        by_stage = defaultdict(list)
        for row in rows:
            if row.get('reference'):
                for stage in row['reference']['stages']:
                    by_stage[stage['stage']].append(stage)
        stages = []
        for number, samples in sorted(by_stage.items()):
            coverage = defaultdict(int)
            for sample in samples:
                for operation, count in sample['firstOperations'].items():
                    coverage[operation] += count
            positive = [[(v, count) for v, count in sample['newPointHistogram'] if v > 0] for sample in samples]
            stages.append(dict(stage=number,
                theoremsPerPoint=distribution([s['densityHistogram'] for s in samples]),
                newPointTheorems=distribution([s['newPointHistogram'] for s in samples]),
                positiveNewPointTheorems=distribution(positive), firstOperations=dict(coverage)))
        finals = [r['reference']['final'] for r in rows if r.get('reference', {}).get('final', {}).get('samples', 0)]
        final = dict(theoremsPerPoint=distribution([f['densityHistogram'] for f in finals]),
            newPointTheorems=distribution([f['newPointHistogram'] for f in finals]),
            positiveNewPointTheorems=distribution([[(v,c) for v,c in f['newPointHistogram'] if v>0] for f in finals]))
        families.append(dict(source=source, runs=len(rows), completed=sum(bool(r.get('reference')) for r in rows),
            requestedFirstOperations=sorted({r['firstOperation'] for r in rows}), stages=stages, final=final))
    return families


def suggested_policy(family, keep_percent):
    if not family['stages']:
        return None
    phases = []
    last_stage = family['stages'][-1]['stage']
    for number in range(1, last_stage + 1):
        stage = next((s for s in family['stages'] if s['stage'] == number), None)
        phase = {}
        if stage:
            density = stage['theoremsPerPoint']
            if density['samples'] >= 30 and density['strata'] >= 3:
                phase['theoremsPerPoint'] = {'min': density['p75']}
        phases.append(phase)
    policy = dict(version=1, policy='soft', seconds=600, seed=17,
                  keepOutsideTargetsPercent=keep_percent, phases=phases)
    positive = family.get('final', {}).get('positiveNewPointTheorems', {'samples':0})
    if positive['samples'] >= 30 and positive['strata'] >= 3:
        policy['final'] = {'newPointTheorems': {'min':1, 'max':max(1,positive['p25'])}}
    return policy


def calibrate(app, inputs, output, *, budget=600, seeds=(17, 29, 43)):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Нужен новый каталог калибровки: ' + str(output))
    if not math.isfinite(budget) or budget < 1 or not seeds:
        raise ValueError('Нужен положительный бюджет и хотя бы один seed')
    catalog = Catalog.read(app.ROOT / 'data/construction-catalog.json')
    stories = [(str(Path(p).resolve()), Story.parse(Path(p).read_text(), catalog)) for p in inputs]
    jobs = [(path, story, op, seed) for path, story in stories for op in story.constructions for seed in seeds]
    random.Random(1729).shuffle(jobs)
    output.mkdir(parents=True)
    records = []
    started = time.monotonic()
    deadline = started + budget
    overhead = 1.5
    for index, (path, story, operation, seed) in enumerate(jobs):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        seconds = max(.1, remaining / (len(jobs) - index) - overhead)
        job = output / f'j{index:04d}'
        (job / 'inputs').mkdir(parents=True)
        prepared = shuffled_story(story, operation, seed)
        prepared.validate(catalog)
        (job / 'inputs/input_calibration.txt').write_text(prepared.text())
        policy = dict(version=1, policy='soft', seconds=seconds, seed=seed,
            keepOutsideTargetsPercent=100, collectReference=True, referenceRootConstruction=operation,
            traceFile=str(job / 'search-trace.jsonl'), referenceFile=str(job / 'search-reference.json'), phases=[{}])
        policy_path = job / 'policy.json'
        policy_path.write_text(json.dumps(policy, indent=2))
        settings = app.job_settings(job)
        runner = settings['ProblemGenerationRunnerSettings']
        for key in list(runner):
            if key.startswith('Write'):
                runner[key] = False
        runner['StreamingOutputFolder'] = None
        runner['LogProgress'] = False
        settings_path = job / 'settings.json'
        settings_path.write_text(json.dumps(settings))
        env = os.environ.copy()
        env.pop('GEOGEN_RELATION_WORKER', None)
        env.update(DOTNET_ROOT=str(app.DOTNET.parent), DOTNET_MULTILEVEL_LOOKUP='0', DOTNET_PROCESSOR_COUNT='1',
            GEOGEN_THEOREM_SEARCH_CONFIG=str(policy_path), GEOGEN_THEOREM_SEARCH_OUTPUT=str(job), GEOGEN_CALIBRATION_ONLY='1')
        began = time.monotonic()
        row = dict(source=path, sourceKey=digest(story.text()), firstOperation=operation, seed=seed,
                   sliceSeconds=seconds, directory=job.name)
        try:
            with (job / 'geogen.log').open('w') as log:
                result = subprocess.run([str(app.DOTNET), str(app.ROOT / 'lib/geogen/GeoGen.MainLauncher.dll'), str(settings_path)],
                    cwd=job, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=max(.1, remaining))
            row['exitCode'] = result.returncode
            reference_path = job / 'search-reference.json'
            if result.returncode == 0 and reference_path.exists():
                row['reference'] = json.loads(reference_path.read_text())
                if not row['reference'].get('unbiased'):
                    raise ValueError('Biased run cannot be used as a reference')
                overhead = .5 * overhead + .5 * max(0, time.monotonic() - began - row['reference']['seconds'])
            else:
                row['error'] = 'Нет полного reference; см. geogen.log'
        except subprocess.TimeoutExpired:
            row['error'] = 'Истёк общий бюджет; частичный запуск не участвует в reference'
        row['wallSeconds'] = time.monotonic() - began
        records.append(row)
        report = dict(version=1, generator='original', selectionBias=False, generationOnly=True,
            weighting='equal nonempty strata within each story and completed symmetry stage',
            plannedRuns=len(jobs), attemptedRuns=len(records), elapsedSeconds=time.monotonic()-started,
            families=summarize(records), runs=records)
        (output / 'calibration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Калибровка {index+1}/{len(jobs)}: {Path(path).name}, {operation}, seed={seed}', flush=True)
    if not records:
        raise RuntimeError('Бюджета не хватило на запуск калибровки')
    for index, family in enumerate(summarize(records)):
        for label, percent in [('neutral',100),('gentle',90),('moderate',70),('strong',30)]:
            policy = suggested_policy(family, percent)
            if policy:
                (output / f'family-{index:02d}-{label}.json').write_text(json.dumps(policy, indent=2)+'\n')
    lines = ['# Калибровка обычного поиска', '',
        'Статистика раздельна по исходным сюжетам и завершённым симметричным этапам.',
        'Каждая непустая серия (первое построение × seed) получает одинаковый вес.',
        'Бюджет ограничивает покрытие аргументов внутри серии; равномерность по всем конфигурациям не заявляется.', '',
        '| Сюжет | Этап | Проб | Серий | Теорем/точку: Q25 / медиана / Q75 | Теорем с новой точкой: Q25 / медиана / Q75 |',
        '|---|---:|---:|---:|---|---|']
    for family in summarize(records):
        for stage in [*family['stages'], dict(stage='final', **family['final'])]:
            a,b=stage['theoremsPerPoint'],stage['newPointTheorems']
            def fmt(d):return ' / '.join(f"{d[k]:.3g}" for k in ['p25','median','p75']) if d.get('samples') else 'нет данных'
            lines.append(f"| {Path(family['source']).name} | {stage['stage']} | {a['samples']} | {a['strata']} | {fmt(a)} | {fmt(b)} |")
    lines += ['', 'Профили — примеры границ по квартилям, а не найденный оптимум.',
        'При <30 наблюдениях или <3 непустых сериях границы соответствующего этапа не выставляются.',
        'Проверьте покрытие первых построений, число успешных серий и устойчивость квартилей по seeds в calibration.json.',
        'Затем сравните neutral/gentle/moderate/strong на независимых сюжетах/seeds и одинаковом времени полного поиска с проверками.',
        'Калибровка считает теоремы и не запускает прувер/Extending; качество задач оценивается в отдельном сравнении.']
    (output / 'CALIBRATION.md').write_text('\n'.join(lines)+'\n')
    return output
