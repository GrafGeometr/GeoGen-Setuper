#!/usr/bin/env python3
"""Commands for the built Geometry suite. Paths are relative to this file."""
from __future__ import annotations

import argparse
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


def job_settings(job: Path):
    settings = json.loads((ROOT / 'data/default-settings.json').read_text(encoding='utf-8-sig'))
    settings['Serilog']['WriteTo'] = [{'Name': 'Console'}]
    settings['ProblemGeneratorInputProviderSettings']['InputFolderPath'] = str(job / 'inputs')
    settings['InferenceRuleProviderSettings']['RuleFolderPath'] = str(ROOT / 'data/inference-rules')
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
    if not VIEWER.is_file():
        raise RuntimeError('Не найден launcher Geometry.')
    print('SMOKE OK: GeoGen → JVM-конвертер → .geo; Extending на контрольных примерах; Geometry упакован.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Поток GeoGen → Extending → .geo (либо --batch для пакетного режима)')
    run.add_argument('input', type=Path)
    run.add_argument('--output', type=Path, required=True, help='Новый каталог запуска')
    run.add_argument('--no-check', action='store_true')
    run.add_argument('--batch', action='store_true', help='Прежний последовательный режим: подборка лучших после завершения поиска')
    run.add_argument('--workers', type=int, default=2, help='Параллельные обработчики находок')
    run.add_argument('--check-ms', type=int, default=1000, help='Лимит одной проверки Extending, мс')
    run.add_argument('--view', action='store_true', help='Сразу открыть список поступающих результатов')
    run.add_argument('--software', action='store_true')
    run.add_argument('--timeout', type=int, default=3600, help='Лимит поиска GeoGen в секундах; после него полученная очередь сохраняется и обрабатывается')
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
    commands.add_parser('doctor', help='Проверить комплект и динамические библиотеки')
    args = parser.parse_args()
    if getattr(args, 'workers', 1) < 1 or getattr(args, 'workers', 1) > 64:
        parser.error('--workers должен быть в диапазоне 1..64')
    if not 1 <= getattr(args, 'check_ms', 1000) <= 3600000 or getattr(args, 'timeout', 1) < 1:
        parser.error('Лимиты времени должны быть положительными; --check-ms не более 3600000')
    if args.command == 'run':
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
        if args.batch:
            run_pipeline(args.input, args.output, extending=not args.no_check, timeout=args.timeout)
        else:
            from streaming import run_stream
            run_stream(args.input, args.output, workers=args.workers, extending=not args.no_check,
                       check_ms=args.check_ms, timeout=args.timeout, view=args.view)
    elif args.command == 'resume':
        from streaming import resume
        resume(args.directory, workers=args.workers, retry_errors=args.retry_errors)
    elif args.command == 'watch':
        if not args.directory.is_dir():
            raise RuntimeError('Нет каталога запуска: ' + str(args.directory))
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise RuntimeError('Для Geometry нужна графическая сессия.')
        if args.software:
            os.environ['SKIKO_RENDER_API'] = 'SOFTWARE'
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
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
