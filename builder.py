#!/usr/bin/env python3
"""Linux x86_64 builder for GeoGen + GeometryExtending + Geometry (stdlib only)."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
DOTNET_VERSION = '7.0.410'
GRADLE_VERSION = '7.2'
GHC_VERSION = '9.6.6'
CABAL_VERSION = '3.10.3.0'
PACKAGES = ['python3', 'git', 'curl', 'ca-certificates', 'unzip', 'xz-utils',
            'build-essential', 'pkg-config', 'libgmp-dev', 'libffi-dev',
            'libncurses-dev', 'libnuma-dev', 'zlib1g-dev', 'libicu-dev',
            'libssl-dev', 'openjdk-17-jdk', 'libx11-6', 'libxext6', 'libxi6',
            'libxrender1', 'libxtst6', 'libgl1', 'libfontconfig1', 'libfreetype6']


class BuildError(RuntimeError):
    pass


def write(path: Path, content: str, executable: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    if executable:
        path.chmod(0o755)


def download(url: str, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    print(f'Скачивание: {url}', flush=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as src, part.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            part.replace(target)
            return
        except (OSError, urllib.error.URLError):
            part.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def proxy_jvm_options(environ):
    """Gradle/JVM does not use HTTP(S)_PROXY automatically. Never log userinfo."""
    options = []
    for scheme in ('http', 'https'):
        raw = environ.get(scheme.upper() + '_PROXY') or environ.get(scheme + '_proxy')
        if raw:
            url = urllib.parse.urlparse(raw)
            if url.username or url.password:
                raise BuildError('Для Gradle нужен прокси без credentials в URL; настройте JVM отдельно.')
            if url.hostname:
                options += [f'-D{scheme}.proxyHost={url.hostname}',
                            f'-D{scheme}.proxyPort={url.port or 80}']
    bypass = environ.get('NO_PROXY', environ.get('no_proxy', ''))
    if bypass:
        hosts = [('*' + h if h.startswith('.') else h) for h in bypass.split(',')
                 if h and '/' not in h]
        options.append('-Dhttp.nonProxyHosts=' + '|'.join(hosts))
    return options


def copy_sources(source: Path, destination: Path):
    # Compilation happens in our workspace, never in the original checkouts.
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        '.git', '.gradle', 'build', 'bin', 'obj', 'dist-newstyle', '.idea',
        'dev_settings.json', 'Logs', '__pycache__'))


class Builder:
    def __init__(self, args):
        self.args = args
        self.state = args.state.resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy()
        self.tools = self.state / 'tools'
        self.work = self.state / 'work'
        self.cache = self.state / 'downloads'
        self.stage = self.state / 'bundle.pending'
        self.logs = self.state / 'logs'
        for directory in (self.tools, self.work, self.cache, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        self.env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
        self.env['DOTNET_NOLOGO'] = '1'
        self.env['MSBUILDDISABLENODEREUSE'] = '1'
        self.env['DOTNET_PROCESSOR_COUNT'] = str(args.jobs)
        self.env.setdefault('DOTNET_CLI_HOME', str(self.state / 'dotnet-home'))
        self.env.setdefault('NUGET_PACKAGES', str(self.state / 'nuget'))
        self.env.setdefault('CABAL_DIR', str(self.state / 'cabal'))
        self.env.setdefault('GRADLE_USER_HOME', str(self.state / 'gradle-home'))
        self.manifest = json.loads((HERE / 'sources.json').read_text())

    def run(self, command, *, cwd=None, name='command', capture=False):
        command = [str(x) for x in command]
        log = self.logs / (name + '.log')
        print(f'[{name}] {shlex.join(command)}\n  лог: {log}', flush=True)
        with log.open('w') as output:
            proc = subprocess.run(command, cwd=cwd, env=self.env, text=True,
                                  stdout=output, stderr=subprocess.STDOUT)
        text = log.read_text(errors='replace')
        if proc.returncode:
            print(text[-8000:], file=sys.stderr)
            raise BuildError(f'{name}: код {proc.returncode}; полный лог: {log}')
        return text if capture else None

    def toolchain(self):
        missing = [x for x in ('git', 'curl', 'gcc', 'make', 'tar', 'xz')
                   if not shutil.which(x, path=self.env['PATH'])]
        if missing:
            raise BuildError(f'Нет {", ".join(missing)}. Выполните ./build.sh deps')
        # JDK must include jpackage and jlink, which generate a private desktop JRE.
        java_home = self.env.get('JAVA_HOME')
        if not java_home:
            javac = shutil.which('javac')
            java_home = str(Path(javac).resolve().parent.parent) if javac else ''
        if not java_home or not (Path(java_home) / 'bin/jpackage').exists():
            raise BuildError('Нужен JDK 17 (включая jpackage). Выполните ./build.sh deps; задайте JAVA_HOME.')
        self.env['JAVA_HOME'] = java_home
        version = self.run([Path(java_home) / 'bin/java', '-version'], name='java-version', capture=True)
        if not re.search(r'version "17[.\"]', version):
            raise BuildError('Требуется JDK 17: выберите JAVA_HOME для этой версии.')
        dotnet = self.args.dotnet or shutil.which('dotnet', path=self.env['PATH'])
        if dotnet:
            sdk = self.run([dotnet, '--list-sdks'], name='dotnet-sdks', capture=True)
            if not any(line.startswith(DOTNET_VERSION + ' ') for line in sdk.splitlines()):
                dotnet = None
        if not dotnet:
            local = self.tools / 'dotnet'
            if not (local / 'sdk' / DOTNET_VERSION).exists():
                if self.args.offline:
                    raise BuildError('Offline: не установлен .NET SDK ' + DOTNET_VERSION)
                script = self.cache / 'dotnet-install.sh'
                download('https://dot.net/v1/dotnet-install.sh', script)
                self.run(['bash', script, '--version', DOTNET_VERSION, '--install-dir', local,
                          '--no-path'], name='install-dotnet')
            dotnet = str(local / 'dotnet')
        self.dotnet = str(Path(dotnet).resolve())
        self.env['DOTNET_ROOT'] = str(Path(self.dotnet).parent)
        gradle = self.args.gradle
        if not gradle:
            local = self.tools / f'gradle-{GRADLE_VERSION}'
            if not (local / 'bin/gradle').exists():
                if self.args.offline:
                    raise BuildError('Offline: Gradle отсутствует; задайте --gradle.')
                archive = self.cache / f'gradle-{GRADLE_VERSION}-bin.zip'
                checksum = archive.with_suffix('.zip.sha256')
                base = f'https://downloads.gradle.org/distributions/{archive.name}'
                download(base + '.sha256', checksum)
                if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != checksum.read_text().strip():
                    download(base, archive)
                if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum.read_text().strip():
                    raise BuildError('Не совпала контрольная сумма Gradle.')
                with zipfile.ZipFile(archive) as zipped:
                    for name in zipped.namelist():
                        if not (self.tools / name).resolve().is_relative_to(self.tools.resolve()):
                            raise BuildError('Небезопасный путь в архиве Gradle.')
                    zipped.extractall(self.tools)
                (local / 'bin/gradle').chmod(0o755)
            gradle = str(local / 'bin/gradle')
        self.gradle = str(Path(gradle).resolve())
        ghcup_bin = self.tools / '.ghcup/bin'
        self.env['PATH'] = os.pathsep.join([str(ghcup_bin), str(Path(self.dotnet).parent),
                                          str(Path(java_home) / 'bin'), self.env['PATH']])
        ghc = shutil.which('ghc', path=self.env['PATH'])
        cabal = shutil.which('cabal', path=self.env['PATH'])
        good_ghc = ghc and self.run([ghc, '--numeric-version'], name='ghc-version', capture=True).strip() == GHC_VERSION
        if not good_ghc or not cabal:
            if self.args.offline:
                raise BuildError('Offline: требуются установленные GHC 9.6.6 и Cabal.')
            self.env['GHCUP_INSTALL_BASE_PREFIX'] = str(self.tools)
            if not (ghcup_bin / 'ghcup').exists():
                script = self.cache / 'ghcup-bootstrap.sh'
                download('https://get-ghcup.haskell.org', script)
                self.env.update(BOOTSTRAP_HASKELL_NONINTERACTIVE='1',
                                BOOTSTRAP_HASKELL_MINIMAL='1',
                                BOOTSTRAP_HASKELL_ADJUST_BASHRC='0')
                self.run(['sh', script], name='install-ghcup')
            if not good_ghc:
                self.run([ghcup_bin / 'ghcup', 'install', 'ghc', GHC_VERSION, '--set'], name='install-ghc')
            if not cabal:
                self.run([ghcup_bin / 'ghcup', 'install', 'cabal', CABAL_VERSION, '--set'], name='install-cabal')
        # Select the pinned GHC explicitly if bootstrapped; do not accidentally use another PATH version.
        self.ghc = str(ghcup_bin / f'ghc-{GHC_VERSION}') if not good_ghc else ghc
        self.cabal = shutil.which('cabal', path=self.env['PATH'])
        if not self.cabal or not Path(self.ghc).exists():
            raise BuildError('Не удалось подготовить GHC/Cabal.')
        print('Инструменты готовы.', flush=True)

    def prepare_sources(self):
        for name, spec in self.manifest.items():
            if self.args.sources:
                src = self.args.sources.resolve() / name
                if not src.is_dir():
                    raise BuildError(f'Нет каталога исходников: {src}')
            else:
                src = self.state / 'sources' / name
                if not src.exists():
                    self.run(['git', 'clone', '--no-checkout', spec['url'], src], name='clone-' + name)
                self.run(['git', '-C', src, 'checkout', '--detach', spec['commit']], name='checkout-' + name)
            if (src / '.git').exists():
                self.manifest[name]['actual_commit'] = self.run(
                    ['git', '-C', src, 'rev-parse', 'HEAD'], name='revision-' + name, capture=True).strip()
                dirty = self.run(['git', '-C', src, 'status', '--porcelain'], name='status-' + name, capture=True)
                self.manifest[name]['dirty'] = bool(dirty.strip())
            copy_sources(src, self.work / name)

    def gradle_run(self, project, *tasks, name):
        command = ['bash', self.gradle, '-p', project, '--no-daemon',
                   f'--max-workers={self.args.jobs}', '-Dorg.gradle.jvmargs=-Xmx1536m',
                   '-Pkotlin.compiler.execution.strategy=in-process']
        command += proxy_jvm_options(self.env)
        command += shlex.split(self.env.get('GEOMETRY_GRADLE_ARGS', ''))
        if self.args.offline:
            command += ['--offline']
        self.run(command + list(tasks), name=name)

    def build(self):
        self.toolchain()
        self.prepare_sources()
        patch = HERE / 'patches/hidden-points.patch'
        # Apply inside the disposable source copy; original checkouts remain untouched.
        self.run(['git', 'apply', '--check', patch], cwd=self.work / 'GeoGen', name='hidden-points-check')
        self.run(['git', 'apply', patch], cwd=self.work / 'GeoGen', name='hidden-points-patch')
        if self.stage.exists():
            shutil.rmtree(self.stage)
        self.stage.mkdir()
        geogen = self.work / 'GeoGen/Source'
        project = geogen / 'Launchers/GeoGen.MainLauncher/GeoGen.MainLauncher.csproj'
        # global.json prevents an unrelated installed SDK from changing the build.
        write(geogen / 'global.json', json.dumps({'sdk': {'version': DOTNET_VERSION, 'rollForward': 'disable'}}))
        offline_restore = []
        if self.args.offline:
            offline_config = self.state / 'offline-nuget.config'
            write(offline_config, '<configuration><packageSources><clear /></packageSources></configuration>')
            offline_restore = ['--configfile', str(offline_config)]
        self.run([self.dotnet, 'restore', project, '--disable-parallel', '-m:1'] + offline_restore,
                 cwd=geogen, name='geogen-restore')
        self.run([self.dotnet, 'publish', project, '--no-restore', '-c', 'Release',
                  '--self-contained', 'false', '-o', self.stage / 'lib/geogen', '-m:1'],
                 cwd=geogen, name='geogen-publish')
        if self.args.test:
            self.run([self.dotnet, 'test', geogen / 'GeoGen.sln', '-c', 'Release', '-m:1'] +
                     ([f'-p:RestoreConfigFile={offline_config}'] if self.args.offline else []),
                     cwd=geogen, name='geogen-tests')
        shutil.copytree(geogen / 'Library/GeoGen.TheoremProver.InferenceRuleProvider/Rules',
                        self.stage / 'data/inference-rules')
        shutil.copytree(geogen / 'Library/GeoGen.TheoremProver.ObjectIntroductionRuleProvider/Rules',
                        self.stage / 'data/object-rules')
        shutil.copytree(geogen / 'Launchers/GeoGen.MainLauncher/Examples/Inputs',
                        self.stage / 'examples')
        shutil.copyfile(geogen / 'Launchers/GeoGen.MainLauncher/default_settings.json',
                        self.stage / 'data/default-settings.json')
        dotroot = Path(self.dotnet).parent
        destination = self.stage / 'lib/dotnet'
        destination.mkdir(parents=True)
        shutil.copy2(self.dotnet, destination / 'dotnet')
        shutil.copytree(dotroot / 'host', destination / 'host')
        runtimes = sorted((dotroot / 'shared/Microsoft.NETCore.App').glob('7.0.*'),
                          key=lambda p: tuple(map(int, p.name.split('.'))))
        if not runtimes:
            raise BuildError('Не найден .NET 7 runtime для комплекта.')
        shutil.copytree(runtimes[-1], destination / 'shared/Microsoft.NETCore.App' / runtimes[-1].name)

        extending = self.work / 'GeometryExtending'
        # Enable multicore RTS without changing the mathematics or hard-coding core count.
        cabal_file = extending / 'GeometryExtending.cabal'
        write(cabal_file, cabal_file.read_text() + '\n    ghc-options: -threaded -rtsopts\n')
        cabal_config = Path(self.env['CABAL_DIR']) / 'config'
        if not cabal_config.exists():
            self.run([self.cabal, 'user-config', 'init'], name='cabal-config')
        config_text = cabal_config.read_text()
        if 'url: http://hackage.haskell.org/' in config_text:
            write(cabal_config, config_text.replace('url: http://hackage.haskell.org/', 'url: https://hackage.haskell.org/'))
        if not self.args.offline:
            self.run([self.cabal, 'update'], name='cabal-update')
        cmd = [self.cabal, 'build', 'all', f'--with-compiler={self.ghc}', f'-j{self.args.jobs}']
        if self.args.offline:
            cmd += ['--offline']
        self.run(cmd, cwd=extending, name='extending-build')
        executable = self.run([self.cabal, 'list-bin', 'exe:GeometryExtending',
                               f'--with-compiler={self.ghc}'], cwd=extending,
                              name='extending-path', capture=True).strip().splitlines()[-1]
        target = self.stage / 'lib/extending/GeometryExtending'
        target.parent.mkdir(parents=True)
        shutil.copy2(executable, target)
        # Preserve the solver's actual dependency versions for subsequent builds.
        self.run([self.cabal, 'freeze', f'--with-compiler={self.ghc}'], cwd=extending, name='extending-freeze')
        shutil.copy2(extending / 'cabal.project.freeze', self.stage / 'data/cabal.project.freeze')

        geometry = self.work / 'Geometry'
        desktop = geometry / 'GeometryDesktop'
        write(desktop / 'src/main/kotlin/Main.kt', (HERE / 'templates/desktop-main.kt').read_text())
        with (desktop / 'build.gradle.kts').open('a') as file:
            file.write('\nsourceSets { main { java.srcDir("../GeometryAndroid/app/src/main/java/com/ibis/geometry/common") } }\n')
        self.gradle_run(desktop, 'build', 'createDistributable', name='geometry-build')
        shutil.copytree(desktop / 'build/compose/binaries/main/app/Geometry Desktop',
                        self.stage / 'lib/geometry')
        converter = self.work / 'converter'
        if converter.exists():
            shutil.rmtree(converter)
        write(converter / 'settings.gradle.kts', 'rootProject.name = "geogen-converter"\n')
        write(converter / 'build.gradle.kts', (HERE / 'templates/converter.gradle.kts').read_text())
        write(converter / 'src/main/kotlin/Main.kt',
              (geometry / 'GeoGenIntegration/src/nativeMain/kotlin/Main.kt').read_text())
        self.gradle_run(converter, 'installDist', name='converter-build')
        shutil.copytree(converter / 'build/install/geogen-converter/lib', self.stage / 'lib/converter')
        self.run([Path(self.env['JAVA_HOME']) / 'bin/jlink', '--add-modules', 'java.base,java.logging',
                  '--strip-debug', '--no-header-files', '--no-man-pages', '--compress=2',
                  '--output', self.stage / 'lib/java'], name='converter-jre')
        shutil.copy2(HERE / 'runtime.py', self.stage / 'runtime.py')
        write(self.stage / 'geometry-suite', '#!/bin/sh\nset -eu\nsuite_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec python3 "$suite_dir/runtime.py" "$@"\n', True)
        write(self.stage / 'BUILD.json', json.dumps({
            'built_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'platform': platform.platform(), 'sources': self.manifest,
            'dotnet_sdk': DOTNET_VERSION, 'ghc': GHC_VERSION,
            'gradle': GRADLE_VERSION, 'jdk': '17',
            'patches': ['HiddenPoints input, generation restriction and symmetry-safe deduplication',
                        'XDG data path and second CLI argument in desktop Main.kt',
                        'desktop common source directory', 'JVM target for original converter',
                        'threaded RTS for Extending']}, indent=2))
        shutil.copy2(HERE / 'README.md', self.stage / 'BUILDER-README.md')
        # A broken build must never replace the last working installation.
        self.run([sys.executable, self.stage / 'runtime.py', 'smoke', '--output', self.work / ('smoke-' + uuid.uuid4().hex[:8])], name='smoke')
        dest = self.args.output.resolve()
        if dest.exists():
            raise BuildError(f'Каталог результата уже существует: {dest}. Укажите новый --output; он не перезаписывается.')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.stage, dest)
        print(f'\nСборка и smoke-проверка завершены.\nЗапуск: {shlex.quote(str(dest / "geometry-suite"))} --help', flush=True)


def install_dependencies():
    if not shutil.which('apt-get'):
        raise BuildError('Автоустановка пакетов поддерживает Debian/Ubuntu. Установите зависимости из README вручную.')
    prefix = [] if os.geteuid() == 0 else ['sudo']
    if prefix and not shutil.which('sudo'):
        raise BuildError('Для установки системных пакетов нужен root или sudo.')
    print('Установка системных пакетов Debian/Ubuntu: ' + ' '.join(PACKAGES), flush=True)
    subprocess.run(prefix + ['apt-get', 'update'], check=True)
    subprocess.run(prefix + ['apt-get', 'install', '-y'] + PACKAGES, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['deps', 'doctor', 'build'], nargs='?', default='build')
    parser.add_argument('--sources', type=Path, help='Каталог с GeoGen/, Geometry/, GeometryExtending/; без него клонируются закреплённые версии')
    parser.add_argument('--state', type=Path, default=HERE / '.builder', help='Инструменты, кэш, рабочие копии и логи')
    parser.add_argument('--output', type=Path, default=HERE / 'dist/geometry-suite', help='Новый каталог готового комплекта')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--dotnet', help='Путь к dotnet с SDK ' + DOTNET_VERSION)
    parser.add_argument('--gradle', help='Путь к bin/gradle версии ' + GRADLE_VERSION)
    parser.add_argument('--offline', action='store_true', help='Сборка из локальных исходников и ранее заполненных кэшей; инструменты должны быть установлены')
    parser.add_argument('--test', action='store_true', help='Также запустить тесты GeoGen')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Нужен Python >= 3.10')
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        parser.error('Эта версия поддерживает Linux x86_64')
    if args.jobs < 1:
        parser.error('--jobs должен быть >= 1')
    if args.offline and not args.sources:
        parser.error('--offline требует --sources')
    if args.command == 'deps':
        install_dependencies()
        return
    if args.command == 'doctor':
        print(f'Linux: {platform.release()}; архитектура: {platform.machine()}; Python: {platform.python_version()}')
        for tool in ('git', 'curl', 'gcc', 'make', 'javac', 'dotnet', 'ghc', 'cabal'):
            print(f'{tool}: {shutil.which(tool) or "не найден (build установит локальные .NET/GHC/Cabal)"}')
        print('JAVA_HOME: ' + os.environ.get('JAVA_HOME', '(не задан)'))
        print('Рекомендуемая база: Debian 12/13 или Ubuntu 22.04/24.04 x86_64; JDK 17; Python 3.10+.')
        return
    dest = args.output.resolve()
    state = args.state.resolve()
    if dest.exists():
        parser.error(f'Каталог --output уже существует: {dest}; выберите новый.')
    if dest == state or dest.is_relative_to(state) or state.is_relative_to(dest):
        parser.error('--state и --output не должны быть вложены друг в друга')
    if args.sources:
        source = args.sources.resolve()
        if source == state or source.is_relative_to(state):
            parser.error('--sources не должен находиться внутри --state')
        for name in ('GeoGen', 'Geometry', 'GeometryExtending'):
            repo = source / name
            if state.is_relative_to(repo) or dest.is_relative_to(repo):
                parser.error('--state/--output не должны находиться внутри исходного репозитория')
    builder = Builder(args)
    with (builder.state / 'build.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BuildError('Другой сборщик уже использует этот --state.')
        builder.build()


if __name__ == '__main__':
    try:
        main()
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Сборка прервана. Логи и кэши сохранены.', file=sys.stderr)
        sys.exit(130)
