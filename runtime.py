#!/usr/bin/env python3
"""Commands for the built Geometry suite. Paths are relative to this file."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
JAVA = ROOT / 'lib/java/bin/java'
DOTNET = ROOT / 'lib/dotnet/dotnet'
EXTENDING = ROOT / 'lib/extending/GeometryExtending'
VIEWER = ROOT / 'lib/geometry/bin/Geometry Desktop'


def execute(command, *, cwd=None, logfile=None, timeout=None):
    env = os.environ.copy()
    env['DOTNET_ROOT'] = str(DOTNET.parent)
    env['DOTNET_MULTILEVEL_LOOKUP'] = '0'
    env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
    print('Запуск: ' + str(command[0]), flush=True)
    if logfile:
        with Path(logfile).open('w') as stream:
            proc = subprocess.run(list(map(str, command)), cwd=cwd, env=env,
                                  stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        if proc.returncode:
            raise RuntimeError(f'Код {proc.returncode}; см. {logfile}')
    else:
        subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True, timeout=timeout)


def convert(input_file: Path, output: Path):
    if not input_file.is_file():
        raise RuntimeError(f'Нет входного файла: {input_file}')
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f'Каталог конвертации должен быть пустым: {output}')
    output.mkdir(parents=True, exist_ok=True)
    execute([JAVA, '-cp', str(ROOT / 'lib/converter/*'), 'MainKt', input_file.resolve(), output.resolve()])
    files = list(output.glob('*.geo'))
    if not files:
        raise RuntimeError('Конвертер не создал .geo')
    return files


def check(directory: Path, *, timeout=300):
    directory = directory.resolve()
    if not directory.is_dir():
        raise RuntimeError(f'Нет каталога: {directory}')
    names = sorted(p.name.removeprefix('BAD_') for p in directory.glob('*.geo'))
    if not names:
        raise RuntimeError('В каталоге нет файлов .geo')
    if len(names) != len(set(names)):
        raise RuntimeError('Одновременно существуют BAD_ и обычная версия одной задачи; разберите конфликт имён.')
    logfile = directory / 'extending.log'
    execute([EXTENDING, str(directory) + os.sep], logfile=logfile, timeout=timeout)
    log = logfile.read_text(errors='replace')
    report = {
        'notice': 'not_rejected означает отсутствие BAD_, а не доказательство и не гарантию успешной проверки. Смотрите лог: исходный Extending не возвращает структурированные статусы ошибок.',
        'rejected': sorted(p.name for p in directory.glob('BAD_*.geo')),
        'not_rejected': sorted(p.name for p in directory.glob('*.geo') if not p.name.startswith('BAD_')),
        'timeouts': [line.strip() for line in log.splitlines() if '!Timeout:' in line],
        'log': str(logfile),
    }
    (directory / 'extending-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'Отбраковано: {len(report["rejected"])}; без BAD_: {len(report["not_rejected"])}; таймауты: {len(report["timeouts"])}')
    print(f'Лог: {logfile}')
    return report


def require_metric_bundle():
    marker = ROOT / 'data/metric-theorems.json'
    if not marker.is_file():
        raise ValueError('Этот комплект не поддерживает дополнительные теоремы; выполните build и запустите новый комплект')
    for relative, expected in json.loads(marker.read_text())['sha256'].items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Несовместимые компоненты дополнительных теорем: ' + relative)


def job_settings(job: Path):
    settings = json.loads((ROOT / 'data/default-settings.json').read_text(encoding='utf-8-sig'))
    extra = os.environ.get('GEOGEN_EXTRA_THEOREMS', '').split(',')
    if extra != ['']:
        require_metric_bundle()
        allowed={'LengthRatio2','LengthRatio3','SquaredLengthRatio2','EqualAngles'}
        if not set(extra)<=allowed: raise ValueError('Unsupported extra theorem type')
        settings['TheoremFindingSettings']['SoughtTheoremTypes'] += [t for t in extra if t not in settings['TheoremFindingSettings']['SoughtTheoremTypes']]
    settings['Serilog']['WriteTo'] = [{'Name': 'Console'}]
    settings['ProblemGeneratorInputProviderSettings']['InputFolderPath'] = str(job / 'inputs')
    settings['InferenceRuleProviderSettings']['RuleFolderPath'] = str(ROOT / 'data/inference-rules')
    if extra != ['']:
        rules = job / 'metric-inference-rules'
        shutil.copytree(ROOT / 'data/inference-rules', rules)
        for source in rules.rglob('*.metric'):
            conclusion=re.search(r'=>\s*(\w+):',source.read_text())
            if conclusion and conclusion.group(1) in extra:
                source.with_suffix('.txt').write_bytes(source.read_bytes())
        settings['InferenceRuleProviderSettings']['RuleFolderPath'] = str(rules)
    settings['ObjectIntroductionRuleProviderSettings']['FilePath'] = str(ROOT / 'data/object-rules/object_introduction_rules.txt')
    runner = settings['ProblemGenerationRunnerSettings']
    for name in runner:
        if name.endswith('Folder'):
            folder = job / 'geogen' / name
            folder.mkdir(parents=True)
            runner[name] = str(folder)
    runner['WriteBestTheoremsContinuously'] = False
    return settings


def run_pipeline(input_file: Path, job: Path, *, extending=True, timeout=300):
    input_file = input_file.resolve()
    job = job.resolve()
    if not input_file.is_file():
        raise RuntimeError(f'Нет исходного сюжета: {input_file}')
    # Never let GeoGen clear a directory belonging to a previous run.
    job.mkdir(parents=True, exist_ok=False)
    (job / 'inputs').mkdir()
    shutil.copyfile(input_file, job / 'inputs/input_story.txt')
    settings = job_settings(job)
    config = job / 'settings.json'
    config.write_text(json.dumps(settings, ensure_ascii=False, indent=2))
    execute([DOTNET, ROOT / 'lib/geogen/GeoGen.MainLauncher.dll', config], cwd=job,
            logfile=job / 'geogen.log', timeout=timeout)
    log = (job / 'geogen.log').read_text(errors='replace')
    # BatchRunner catches per-input exceptions and can still return exit code zero.
    if "Couldn't perform the algorithm" in log or '[ERR]' in log or '[FTL]' in log:
        raise RuntimeError(f'GeoGen сообщил об ошибке; см. {job / "geogen.log"}')
    best = Path(settings['ProblemGenerationRunnerSettings']['ReadableBestTheoremFolder'])
    output = job / 'geo'
    output.mkdir()
    for text in sorted(best.glob('*.txt')):
        if not text.read_text().strip():
            continue
        # Original converter can leave a partial file on error; stage each conversion.
        with tempfile.TemporaryDirectory(prefix='convert-', dir=job) as tmp:
            files = convert(text, Path(tmp))
            for file in files:
                shutil.move(str(file), output / file.name)
    generated = list(output.glob('*.geo'))
    if extending and generated:
        check(output, timeout=timeout)
    print(f'Получено .geo: {len(generated)}. Результаты: {job}')
    return job


def smoke(output: Path):
    if output.exists():
        raise RuntimeError(f'Каталог smoke уже существует: {output}; задайте новый.')
    output.mkdir(parents=True)
    # Exercise the original converter, the Haskell filter, the private JRE, and .NET.
    fixture = output / 'fixture.txt'
    fixture.write_text('''---------
Theorem 1
---------

Triangle: A, B, C
D = Midpoint(A, B)

EqualLineSegments: A, D, D, B - total ranking 1

  Symmetry                         weight = 1 ranking = 1 contribution = 1
  Level                            weight = 1 ranking = 1 contribution = 1
  NumberOfCyclicQuadrilaterals      weight = 1 ranking = 1 contribution = 1
  NumberOfTheorems                 weight = 1 ranking = 1 contribution = 1
''')
    files = convert(fixture, output / 'converter')
    if '!EqualLineSegments: A, D, D, B' not in files[0].read_text():
        raise RuntimeError('Конвертер потерял исходное утверждение.')
    checks = output / 'checks'
    checks.mkdir()
    for name, fact in [('essential.geo', 'EqualLineSegments: A, D, D, B'),
                       ('redundant.geo', 'CollinearPoints: A, D, B')]:
        (checks / name).write_text('!Triangle: A, B, C\n!D = Midpoint(A, B)\n!' + fact + '\n!\n!\n!\n!\n')
    report = check(checks, timeout=60)
    if report['rejected'] != ['BAD_redundant.geo'] or report['not_rejected'] != ['essential.geo']:
        raise RuntimeError('Неожиданный результат smoke-проверки Extending.')
    # A construction's defining midpoint/midline facts must be filtered, and
    # Incidence must survive conversion and reach the structured Haskell checker.
    custom_fixture = output / 'custom-fixture.txt'
    custom_fixture.write_text(fixture.read_text().replace(
        'D = Midpoint(A, B)', 'l = Midline(A, B, C)\nD = HeightMiddle(A, B, C)').replace(
        'EqualLineSegments: A, D, D, B', 'Incidence: D, l'))
    custom_files = convert(custom_fixture, output / 'custom-converter')
    response = subprocess.run([str(EXTENDING), '--check-file', str(custom_files[0]), '2000'],
                              text=True, capture_output=True, timeout=7, check=True)
    if json.loads(response.stdout).get('status') != 'rejected':
        raise RuntimeError('HeightMiddle/Midline must be rejected as a defining construction fact.')
    # Exercise initial contact points through the downstream checker: unlike a
    # generated triviality, these definitions cannot disappear in GeoGen's prover.
    contact_fixture = output / 'initial-contacts.txt'
    contact_fixture.write_text(fixture.read_text().replace('D = Midpoint(A, B)',
        'D = IncircleTangentPoint(A, B, C)\nE = IncircleTangentPoint(B, A, C)\nF = IncircleTangentPoint(C, A, B)').replace(
        'EqualLineSegments: A, D, D, B', 'CollinearPoints: B, C, D'))
    contact_files = convert(contact_fixture, output / 'initial-contacts-converter')
    response = subprocess.run([str(EXTENDING), '--check-file', str(contact_files[0]), '2000'],
                              text=True, capture_output=True, timeout=7, check=True)
    status = json.loads(response.stdout)
    if status.get('status') != 'rejected':
        raise RuntimeError('Проверка исходных точек касания завершилась неправильно: ' + str(status))
    # Truth validation is distinct from construction weakening.
    validation = subprocess.run([str(EXTENDING),'--validate-file',str(files[0]),'2000','3','1729'],text=True,capture_output=True,timeout=5,check=True)
    if json.loads(validation.stdout).get('status') != 'sampled-valid':
        raise RuntimeError('Exact original theorem validation failed.')
    false_file = output / 'false-theorem.geo'
    false_file.write_text(files[0].read_text().replace('!EqualLineSegments: A, D, D, B','!EqualLineSegments: A, B, B, C'))
    validation = subprocess.run([str(EXTENDING),'--validate-file',str(false_file),'2000','3','1729'],text=True,capture_output=True,timeout=5,check=True)
    if json.loads(validation.stdout).get('status') != 'counterexample':
        raise RuntimeError('Validator failed to refute a false theorem.')
    run_pipeline(ROOT / 'examples/input_projections.txt', output / 'job', extending=False, timeout=120)
    if not list((output / 'job/geo').glob('*.geo')):
        raise RuntimeError('Пример GeoGen не дал ни одной задачи для конвертации.')
    # Exactly one new midpoint is reachable through visible D/E, despite hidden A/B/C.
    hidden_job = run_pipeline(ROOT / 'examples/input_hidden_points.txt', output / 'hidden-job', extending=False, timeout=120)
    hidden_log = (hidden_job / 'geogen.log').read_text()
    if not re.search(r"Generated configurations: 1(?:\r?\n|$)", hidden_log):
        raise RuntimeError('HiddenPoints did not restrict the full GeoGen pipeline to one configuration.')
    from streaming import run_stream
    import sqlite3
    live_job = run_stream(ROOT / 'examples/input_projections.txt', output / 'stream-job', timeout=120)
    with sqlite3.connect(live_job / 'tasks.sqlite') as db:
        ready = db.execute("SELECT count(*) FROM tasks WHERE state='ready'").fetchone()[0]
        pending = db.execute("SELECT count(*) FROM tasks WHERE state IN ('queued','processing')").fetchone()[0]
    if ready < 1 or pending:
        raise RuntimeError('Поток не выдал проверенных задач или не завершил очередь.')
    custom_story = output / 'custom-story.txt'
    custom_story.write_text('''Constructions:

 HeightMiddle

Initial configuration:

 Triangle: A, B, C
 l = Midline(A, B, C)

Iterations: 1
MaximalPoints: 1
MaximalLines: 0
MaximalCircles: 0
SymmetryGenerationMode: GenerateBothSymmetricAndAsymmetric
''')
    custom_job = run_stream(custom_story, output / 'custom-stream-job', timeout=120)
    with sqlite3.connect(custom_job / 'tasks.sqlite') as db:
        if db.execute('SELECT count(*) FROM tasks').fetchone()[0]:
            raise RuntimeError('GeoGen still publishes the defining HeightMiddle/Midline fact.')
    contact_story = output / 'contact-story.txt'
    contact_story.write_text('''Constructions:

 IncircleTangentPoint

Initial configuration:

 Triangle: A, B, C
 c = Incircle(A, B, C)

Iterations: 1
MaximalPoints: 1
MaximalLines: 0
MaximalCircles: 0
SymmetryGenerationMode: GenerateBothSymmetricAndAsymmetric
''')
    contact_job = run_stream(contact_story, output / 'contact-stream-job', timeout=120)
    with sqlite3.connect(contact_job / 'tasks.sqlite') as db:
        if db.execute('SELECT count(*) FROM tasks').fetchone()[0]:
            raise RuntimeError('GeoGen still publishes defining incircle contact facts.')
    # A bounded user-family campaign exercises two real producers and one shared catalog/queue.
    from discovery import create as create_campaign, run as resume_campaign
    from families import Catalog
    catalog = Catalog.read(ROOT / 'data/construction-catalog.json')
    if len(catalog.constructions) != 72 or catalog.layouts['LineAndPoint']['pipelineSupported']:
        raise RuntimeError('Неправильный каталог возможностей.')
    campaign_family = output / 'family.json'
    campaign_family.write_text(json.dumps(dict(version=1, stories=[str(ROOT / 'examples/input_projections.txt')],
        growth={'maxAddedDefinitions': 0}, search={'maxDepth': 1, 'maxJobs': 2, 'sliceSeconds': 10, 'maxPaletteSize': 1})))
    campaign = create_campaign(campaign_family, output / 'campaign', budget=15, search_workers=2, check_workers=2)
    with sqlite3.connect(campaign / 'tasks.sqlite') as db:
        if db.execute("SELECT count(*) FROM search_jobs WHERE state='failed'").fetchone()[0]:
            raise RuntimeError('GeoGen failed in family campaign.')
        if not db.execute("SELECT count(*) FROM tasks WHERE state='ready'").fetchone()[0]:
            raise RuntimeError('Family campaign produced no checked diagrams.')
        if db.execute("SELECT count(*) FROM tasks WHERE state IN ('queued','processing','error')").fetchone()[0]:
            raise RuntimeError('Family campaign left errors or unfinished checks.')
    before = (campaign / 'results.tsv').read_bytes()
    resume_campaign(campaign)
    if (campaign / 'results.tsv').read_bytes() != before:
        raise RuntimeError('Completed campaign was modified by resume.')
    if not VIEWER.is_file():
        raise RuntimeError('Не найден launcher Geometry.')
    diagram = subprocess.run([str(VIEWER),'--check-geo',str(files[0])],text=True,capture_output=True,timeout=30,check=True)
    diagram_result = json.loads(diagram.stdout)
    if diagram_result.get('checked') != 1 or diagram_result.get('failures'):
        raise RuntimeError('Geometry could not evaluate its control diagram headlessly.')
    print('SMOKE OK: GeoGen → JVM-конвертер → .geo; Extending на контрольных примерах; Geometry упакован.')


def require_search_bundle(policy='density', *, rank=False):
    import hashlib
    marker_path=ROOT/'data/native-theorem-search.json'
    if not marker_path.exists():raise ValueError('Пересоберите комплект: нет native theorem search')
    marker=json.loads(marker_path.read_text())
    dll=ROOT/'lib/geogen/GeoGen.ProblemGenerator.dll'
    if marker.get('version')!=1 or marker.get('sha256')!=hashlib.sha256(dll.read_bytes()).hexdigest():
        raise ValueError('Маркер native theorem search не соответствует DLL')
    if policy=='soft' and 'original-soft-search-v1' not in marker.get('features',[]):
        raise ValueError('Пересоберите комплект: DLL не поддерживает мягкий отбор в исходном GeoGen')
    if rank and 'exact-rank-interval-v1' not in marker.get('features',[]):
        raise ValueError('Пересоберите комплект: DLL не поддерживает точный ранговый интервал')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Поток GeoGen → Extending → .geo (либо --batch для пакетного режима)')
    run.add_argument('input', type=Path)
    run.add_argument('--output', type=Path, required=True, help='Новый каталог запуска')
    run.add_argument('--no-check', action='store_true')
    run.add_argument('--quality-checks', action='store_true', help='Проверять истинность на независимых точных картинках')
    run.add_argument('--standard-checks', action='store_true', help='Отсеивать короткие точные угловые/аффинные следствия')
    run.add_argument('--search-config',type=Path,help='JSON-настройки поиска по числу теорем; ядро C#')
    run.add_argument('--experimental-relations',action='store_true',help='Ограниченно выбирать построения по совместным связям внутри GeoGen (эксперимент)')
    run.add_argument('--relation-target',type=float,default=.2)
    run.add_argument('--relation-proposals',type=int,default=24)
    run.add_argument('--relation-keep',type=int,default=6)
    run.add_argument('--relation-seed',type=int,default=17)
    run.add_argument('--relation-policy',choices=['joint','uniform'],default='joint')
    run.add_argument('--batch', action='store_true', help='Прежний последовательный режим: подборка лучших после завершения поиска')
    run.add_argument('--workers', type=int, default=2, help='Параллельные обработчики находок')
    run.add_argument('--check-ms', type=int, default=1000, help='Лимит одной проверки Extending, мс')
    run.add_argument('--view', action='store_true', help='Сразу открыть список поступающих результатов')
    run.add_argument('--software', action='store_true')
    run.add_argument('--extra-theorems', nargs='+', choices=['LengthRatio2','LengthRatio3','SquaredLengthRatio2','EqualAngles'], help='Дополнительные типы утверждений; ядро C#')
    run.add_argument('--timeout', type=int, default=3600, help='Лимит поиска GeoGen в секундах; после него полученная очередь сохраняется и обрабатывается')
    discover = commands.add_parser('discover', help='Исследовать пользовательское семейство сюжетов')
    discover.add_argument('family', type=Path)
    discover.add_argument('--output', type=Path, required=True)
    discover.add_argument('--budget', default='10m', help='Общий бюджет поиска: 30s, 10m, 8h')
    discover.add_argument('--search-workers', type=int, default=2)
    discover.add_argument('--check-workers', type=int, default=2)
    discover.add_argument('--check-ms', type=int, default=1000)
    discover.add_argument('--view', action='store_true')
    discover.add_argument('--software', action='store_true')
    discover.add_argument('--extra-theorems', nargs='+', choices=['LengthRatio2','LengthRatio3','SquaredLengthRatio2','EqualAngles'])
    commands.add_parser('catalog', help='Показать каталог построений и поддержку исходных конфигураций')
    calibration=commands.add_parser('calibrate-search',help='Контрольная статистика исходного GeoGen по сюжетам, seeds и первым построениям')
    calibration.add_argument('inputs',type=Path,nargs='+')
    calibration.add_argument('--output',type=Path,required=True)
    calibration.add_argument('--budget',default='10m')
    calibration.add_argument('--seeds',type=int,nargs='+',default=[17,29,43])
    for name in ('experimental-relations','experimental-sample'):
        experiment=commands.add_parser(name,help='Эксперимент: численные совместные связи / выбор начальных построений')
        experiment.add_argument('input',type=Path)
        experiment.add_argument('--catalog',type=Path,default=ROOT/'data/construction-catalog.json')
        experiment.add_argument('--seed',type=int,default=17)
        experiment.add_argument('--samples',type=int,default=12)
        if name=='experimental-relations':
            experiment.add_argument('--pair',nargs=2)
            experiment.add_argument('--all-pairs',action='store_true')
            experiment.add_argument('--pair-budget',type=int,default=24)
        else:
            experiment.add_argument('--output',type=Path,required=True)
            experiment.add_argument('--steps',type=int,default=1)
            experiment.add_argument('--proposal-budget',type=int,default=24)
            experiment.add_argument('--target-strength',type=float,default=.2)
            experiment.add_argument('--max-depth',type=int)
    conv = commands.add_parser('convert', help='Подборка ReadableBestTheorems → .geo')
    conv.add_argument('input', type=Path)
    conv.add_argument('output', type=Path)
    checking = commands.add_parser('check', help='Extending: переименовывает отбракованные файлы в BAD_...')
    checking.add_argument('directory', type=Path)
    checking.add_argument('--timeout', type=int, default=300)
    view = commands.add_parser('view', help='Открыть Geometry с файлом .geo')
    view.add_argument('file', type=Path, nargs='?')
    view.add_argument('--software', action='store_true', help='Программный рендеринг для VM/неподдерживаемого GPU')
    testing = commands.add_parser('smoke', help='Проверить сборку без графического дисплея')
    testing.add_argument('--output', type=Path, required=True)
    watch = commands.add_parser('watch', help='Открыть пополняющийся список результатов в Geometry')
    watch.add_argument('directory', type=Path)
    watch.add_argument('--software', action='store_true')
    resume = commands.add_parser('resume', help='Продолжить обработку очереди без повторного поиска GeoGen')
    resume.add_argument('directory', type=Path)
    resume.add_argument('--workers', type=int, default=2)
    resume.add_argument('--retry-errors', action='store_true')
    resume.add_argument('--recheck', action='store_true', help='Обновить проверяющую сборку кампании и перепроверить сохранённые кандидаты')
    resume.add_argument('--budget', help='Добавочный бюджет поиска для кампании, например 10m')
    resume.add_argument('--search-workers', type=int)
    resume.add_argument('--check-workers', type=int)
    audit = commands.add_parser('audit', help='Проверить качество готовых задач, сохранив подробный отчёт')
    audit.add_argument('directory', type=Path)
    audit.add_argument('--samples', type=int, default=8)
    audit.add_argument('--milliseconds', type=int, default=2500)
    audit.add_argument('--workers', type=int, default=2)
    audit.add_argument('--limit', type=int)
    audit.add_argument('--diagrams', action='store_true', help='Проверить вычислимость картинок самим Geometry без окна')
    audit.add_argument('--triangle-domain', choices=['generic','acute'], default='generic')
    commands.add_parser('doctor', help='Проверить комплект и динамические библиотеки')
    args = parser.parse_args()
    if getattr(args, 'workers', 1) < 1 or getattr(args, 'workers', 1) > 64:
        parser.error('--workers должен быть в диапазоне 1..64')
    if not 1 <= getattr(args, 'check_ms', 1000) <= 3600000 or getattr(args, 'timeout', 1) < 1:
        parser.error('Лимиты времени должны быть положительными; --check-ms не более 3600000')
    for key in ('search_workers', 'check_workers'):
        value = getattr(args, key, None)
        if value is not None and not 1 <= value <= 32:
            parser.error('--' + key.replace('_', '-') + ' должен быть в диапазоне 1..32')

    if args.command=='calibrate-search':
        from search_calibration import calibrate
        from discovery import seconds
        require_search_bundle('soft')
        result=calibrate(sys.modules[__name__],args.inputs,args.output,budget=seconds(args.budget),seeds=tuple(args.seeds))
        print('Калибровка: '+str(result/'CALIBRATION.md'))
    elif args.command in ('experimental-relations','experimental-sample'):
        from families import Catalog,Story
        from experimental_relations import analyze,analyze_configuration,sample_prefix
        if not 8<=args.samples<=32:parser.error('--samples: 8..32')
        catalog=Catalog.read(args.catalog)
        story=Story.parse(args.input.read_text(),catalog)
        if args.command=='experimental-relations':
            if args.all_pairs and args.pair:parser.error('Choose --pair or --all-pairs')
            result=analyze_configuration(story,catalog,seed=args.seed,samples=args.samples,pair_budget=args.pair_budget) if args.all_pairs else analyze(story,catalog,args.pair,args.seed,args.samples)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        else:
            if args.output.exists():raise FileExistsError('Каталог эксперимента уже существует')
            result,trace=sample_prefix(story,catalog,steps=args.steps,proposal_budget=args.proposal_budget,
                                      target=args.target_strength,seed=args.seed,samples=args.samples,max_depth=args.max_depth)
            args.output.mkdir(parents=True)
            (args.output/'source.txt').write_text(story.text())
            (args.output/'sampled.txt').write_text(result.text())
            (args.output/'sampling.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2))
            print(json.dumps(dict(experimental=True,input=str(args.output/'sampled.txt'),
                                  proposed=sum(s['proposed'] for s in trace['trace']),
                                  drawn=sum(s['drawn'] for s in trace['trace']),scope=trace['scope']),ensure_ascii=False))
    elif args.command == 'discover':
        if args.extra_theorems: os.environ['GEOGEN_EXTRA_THEOREMS']=','.join(args.extra_theorems)
        from discovery import create, seconds
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
        if args.view and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            parser.error('Для --view нужна графическая сессия')
        create(args.family, args.output, budget=seconds(args.budget), search_workers=args.search_workers,
               check_workers=args.check_workers, check_ms=args.check_ms, view=args.view)
    elif args.command == 'catalog':
        print((ROOT / 'data/construction-catalog.json').read_text())
    elif args.command == 'run':
        if args.extra_theorems: os.environ['GEOGEN_EXTRA_THEOREMS']=','.join(args.extra_theorems)
        if args.search_config:
            if args.experimental_relations:parser.error('--search-config нельзя сочетать с --experimental-relations')
            if not args.search_config.is_file():parser.error('Нет файла --search-config')
            search_config={k.lower():v for k,v in json.loads(args.search_config.read_text()).items()}
            search_policy=search_config.get('policy','density')
            try:require_search_bundle(search_policy,rank=('rankintervals' in search_config or 'finalrankinterval' in search_config))
            except ValueError as error:parser.error(str(error))
            os.environ['GEOGEN_THEOREM_SEARCH_CONFIG']=str(args.search_config.resolve())
            os.environ['GEOGEN_THEOREM_SEARCH_OUTPUT']=str(args.output.resolve())
        if args.experimental_relations:
            from families import Catalog,Story
            from experimental_relations import configure_worker
            source=Story.parse(args.input.read_text(),Catalog.read(ROOT/'data/construction-catalog.json'))
            if source.layout not in ('Triangle','RightTriangle'):parser.error('Experimental sampler: Triangle/RightTriangle only')
            configure_worker(ROOT,target=args.relation_target,proposals=args.relation_proposals,
                             keep=args.relation_keep,seed=args.relation_seed,policy=args.relation_policy)
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
        if args.batch and (args.standard_checks or args.quality_checks):
            parser.error('--standard-checks/--quality-checks поддержаны в потоковом режиме')
        if args.batch:
            run_pipeline(args.input, args.output, extending=not args.no_check, timeout=args.timeout)
        else:
            from streaming import run_stream
            run_stream(args.input, args.output, workers=args.workers, extending=not args.no_check,
                       check_ms=args.check_ms, timeout=args.timeout, view=args.view, standard=args.standard_checks, quality_checks=args.quality_checks)
    elif args.command == 'resume':
        metadata = json.loads((args.directory / 'run-state.json').read_text())
        if metadata.get('kind') == 'campaign':
            from discovery import run, seconds
            run(args.directory, extra_budget=seconds(args.budget) if args.budget else 0,
                search_workers=args.search_workers, check_workers=args.check_workers, retry_errors=args.retry_errors, recheck=args.recheck)
        else:
            if args.budget or args.search_workers or args.check_workers or args.recheck:
                parser.error('Дополнительный бюджет/работники поиска поддержаны только для кампаний')
            from streaming import resume
            resume(args.directory, workers=args.workers, retry_errors=args.retry_errors)
    elif args.command == 'watch':
        if not args.directory.is_dir():
            raise RuntimeError('Нет каталога запуска: ' + str(args.directory))
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise RuntimeError('Для Geometry нужна графическая сессия.')
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
        from streaming import refresh_results
        refresh_results(args.directory)
        execute([VIEWER, '--watch', args.directory.resolve()])
    elif args.command == 'convert':
        convert(args.input, args.output)
    elif args.command == 'check':
        check(args.directory, timeout=args.timeout)
    elif args.command == 'view':
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise RuntimeError('Для Geometry нужна графическая сессия (DISPLAY/WAYLAND_DISPLAY).')
        if args.file and not args.file.is_file():
            raise RuntimeError(f'Нет файла: {args.file}')
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
        execute([VIEWER] + ([args.file.resolve()] if args.file else []))
    elif args.command == 'smoke':
        smoke(args.output.resolve())
    elif args.command == 'audit':
        if not 1 <= args.samples <= 100 or not 1 <= args.milliseconds <= 3600000 or (args.limit is not None and args.limit < 1):
            parser.error('Неверные параметры audit')
        from quality import audit
        audit(args.directory, EXTENDING, samples=args.samples, milliseconds=args.milliseconds, workers=args.workers, limit=args.limit, triangle_domain=args.triangle_domain, diagrams=args.diagrams)
    elif args.command == 'doctor':
        for file in (JAVA, DOTNET, EXTENDING, VIEWER):
            if not file.exists():
                raise RuntimeError(f'Нет компонента: {file}')
        execute([DOTNET, '--list-runtimes'])
        execute([JAVA, '-version'])
        execute([EXTENDING, '+RTS', '--info'])
        for file in (EXTENDING, VIEWER):
            result = subprocess.run(['ldd', str(file)], text=True, capture_output=True)
            print(result.stdout)
            if 'not found' in result.stdout:
                raise RuntimeError('Не хватает системных библиотек; см. вывод ldd и README.')
        print('Комплект найден. GUI проверяется запуском команды view в графической сессии.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
