"""Typed story families. Input definitions stay immutable; variants have explicit provenance."""
from __future__ import annotations
from dataclasses import dataclass, replace
import glob
import hashlib
import itertools
import json
from pathlib import Path
import random
import re

VERSION = 1
LAYOUT_RELAXATIONS = {'RightTriangle': 'Triangle', 'CyclicQuadrilateral': 'Quadrilateral'}
SYMMETRIES = ('GenerateBothSymmetricAndAsymmetric', 'GenerateOnlySymmetric', 'GenerateOnlyFullySymmetric')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name}: требуется целое число {low}..{high}')
    return value


class Catalog:
    def __init__(self, data):
        if data.get('version') != VERSION:
            raise ValueError('Неподдерживаемая версия каталога построений')
        self.data = data
        self.constructions = {c['name']: c for c in data['constructions']}
        self.layouts = {c['name']: c for c in data['layouts']}
        if not self.constructions or not self.layouts:
            raise ValueError('Пустой каталог возможностей')

    @classmethod
    def read(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def construction(self, name):
        try:
            return self.constructions[name]
        except KeyError:
            raise ValueError('Неизвестное построение: ' + name) from None

    def arguments_key(self, op, arguments):
        it = iter(arguments)
        def group(parameter):
            if 'type' in parameter:
                return next(it)
            return tuple(sorted((group(parameter['item']) for _ in range(parameter['count'])), key=repr))
        try:
            result = tuple(group(p) for p in self.construction(op)['parameters'])
            if next(it, None) is not None:
                raise ValueError('Лишние аргументы: ' + op)
            return result
        except StopIteration:
            raise ValueError('Недостаточно аргументов: ' + op) from None


def parse_configuration(text):
    lines = [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith('#')]
    if not lines or ':' not in lines[0]:
        raise ValueError('Ожидается исходная конфигурация Layout: A, B, ...')
    layout, names = lines[0].split(':', 1)
    names = tuple(s.strip() for s in names.split(','))
    definitions = []
    for line in lines[1:]:
        match = re.fullmatch(r'(\w+)\s*=\s*(\w+)\s*\((.*)\)', line)
        if not match:
            raise ValueError('Некорректное определение: ' + line)
        name, op, args = match.groups()
        arguments = tuple(s.strip() for s in re.sub(r'[{}\[\]]', '', args).split(',')) if args else ()
        definitions.append((name, op, arguments))
    return layout.strip(), names, tuple(definitions)


@dataclass(frozen=True)
class Story:
    layout: str
    names: tuple
    definitions: tuple
    constructions: tuple
    iterations: int = 2
    limits: tuple = (3, 3, 3)
    symmetry: str = 'GenerateBothSymmetricAndAsymmetric'
    hidden: tuple = ()

    @classmethod
    def parse(cls, text, catalog):
        lines = [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith('#')]
        if not lines or lines[0] != 'Constructions:' or 'Initial configuration:' not in lines:
            raise ValueError('Ожидаются секции Constructions и Initial configuration')
        start = lines.index('Initial configuration:')
        stop = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('Iterations:')), None)
        if stop is None:
            raise ValueError('Нет Iterations')
        options = {}
        for line in lines[stop:]:
            key, value = line.split(':', 1)
            if key in options or key not in ('Iterations', 'MaximalPoints', 'MaximalLines', 'MaximalCircles', 'SymmetryGenerationMode'):
                raise ValueError('Неизвестный или повторный параметр: ' + key)
            options[key] = value.strip()
        hidden = [s for s in lines[start + 1:stop] if s.startswith('HiddenPoints:')]
        if len(hidden) > 1:
            raise ValueError('Повторный HiddenPoints')
        layout, names, definitions = parse_configuration('\n'.join(s for s in lines[start + 1:stop] if not s.startswith('HiddenPoints:')))
        result = cls(layout, names, definitions, tuple(dict.fromkeys(lines[1:start])),
                     int(options['Iterations']), tuple(int(options.get('Maximal' + typ, '2147483647')) for typ in ('Points', 'Lines', 'Circles')),
                     options.get('SymmetryGenerationMode', SYMMETRIES[0]),
                     tuple(s.strip() for s in hidden[0].split(':', 1)[1].split(',') if s.strip()) if hidden else ())
        result.validate(catalog)
        return result

    def validate(self, catalog):
        layout = catalog.layouts.get(self.layout)
        if not layout or not layout.get('pipelineSupported'):
            raise ValueError('Исходная конфигурация не поддержана всем комплектом: ' + self.layout)
        if len(self.names) != len(layout['arguments']) or len(set(self.names)) != len(self.names):
            raise ValueError('Неправильные исходные объекты')
        types = dict(zip(self.names, layout['arguments']))
        for name, op, args in self.definitions:
            construction = catalog.construction(op)
            if name in types or [types.get(a) for a in args] != construction['arguments']:
                raise ValueError('Несовместимые типы/имя в определении: ' + name)
            if len(set(args)) != len(args):
                raise ValueError('Повторные аргументы: ' + name)
            types[name] = construction['output']
        if not all(n.isalnum() for n in types):
            raise ValueError('Имена объектов должны состоять из букв и цифр')
        if any(types.get(p) != 'Point' for p in self.hidden) or len(set(self.hidden)) != len(self.hidden):
            raise ValueError('HiddenPoints содержит неизвестную или повторную точку')
        if not self.constructions:
            raise ValueError('Пустой список построений')
        for name in self.constructions:
            catalog.construction(name)
        integer(self.iterations, 'Iterations', 1, 20)
        for value in self.limits:
            integer(value, 'MaximalObjects', 0, 2147483647)
        if self.symmetry not in SYMMETRIES:
            raise ValueError('Неизвестный режим симметрии')
        return types

    def configuration(self):
        return self.layout + ': ' + ', '.join(self.names) + '\n' + ''.join(
            f'{name} = {op}({", ".join(args)})\n' for name, op, args in self.definitions)

    def text(self):
        hidden = 'HiddenPoints: ' + ', '.join(self.hidden) + '\n' if self.hidden else ''
        return ('Constructions:\n' + '\n'.join(self.constructions) + '\n\nInitial configuration:\n' +
                self.configuration() + hidden + f'\nIterations: {self.iterations}\n' +
                ''.join(f'Maximal{typ}: {value}\n' for typ, value in zip(('Points', 'Lines', 'Circles'), self.limits)) +
                f'SymmetryGenerationMode: {self.symmetry}\n')


def canonical_event(event, catalog):
    """Structural equivalence including all hypotheses, set arguments and base symmetries."""
    layout, names, definitions = parse_configuration(event['configuration'])
    perms = [names]
    if layout == 'Triangle':
        perms = itertools.permutations(names)
    elif layout == 'LineSegment':
        perms = [names, names[::-1]]
    elif layout == 'RightTriangle':
        perms = [names, (names[0], names[2], names[1])]
    elif layout in ('Quadrilateral', 'CyclicQuadrilateral'):
        perms = [seq[i:] + seq[:i] for seq in (names, names[::-1]) for i in range(len(names))]
    outputs = []
    for perm in perms:
        objects = {n: ('free', i) for i, n in enumerate(perm)}
        for name, op, args in definitions:
            objects[name] = (op, catalog.arguments_key(op, [objects[a] for a in args]))
        def obj(text):
            text = text.strip()
            if text.startswith('[') and text.endswith(']'):
                return ('line', tuple(sorted((objects[a.strip()] for a in text[1:-1].split(',')), key=repr)))
            if text.startswith('(') and text.endswith(')'):
                return ('circle', tuple(sorted((objects[a.strip()] for a in text[1:-1].split(',')), key=repr)))
            # Explicit and point-defined lines/circles share the same definition.
            value = objects[text]
            if value[0] == 'LineFromPoints':
                return ('line', tuple(sorted(value[1][0], key=repr)))
            if value[0] == 'Circumcircle':
                return ('circle', tuple(sorted(value[1][0], key=repr)))
            return value
        kind, goal = event['theorem'].split(':', 1)
        tokens = re.findall(r'\[[^\]]+\]|\([^\)]+\)|\w+', goal)
        args = [obj(t) for t in tokens]
        if kind == 'EqualLineSegments':
            if len(args) != 4:
                raise ValueError('Неправильное равенство отрезков')
            args = [tuple(sorted(args[:2], key=repr)), tuple(sorted(args[2:], key=repr))]
        outputs.append(repr((layout, tuple(sorted(objects.values(), key=repr)), kind.strip(), tuple(sorted(args, key=repr)))))
    return digest(min(outputs))


@dataclass
class Family:
    config: dict
    stories: list
    catalog: Catalog

    @classmethod
    def read(cls, path, catalog):
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Файл семейства должен содержать JSON-объект')
        unknown = set(data) - {'version', 'name', 'stories', 'allowedConstructions', 'growth', 'search', 'includeRelaxations', 'seed'}
        if unknown or data.get('version') != 1:
            raise ValueError('Неизвестные поля/версия семейства: ' + ', '.join(sorted(unknown)))
        patterns = data.get('stories')
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
            raise ValueError('stories должен содержать пути или маски story-файлов')
        files = []
        for pattern in patterns:
            found = sorted(glob.glob(str(path.parent / pattern)))
            if not found:
                raise ValueError('Не найдены сюжеты: ' + pattern)
            files.extend(found)
        stories = [Story.parse(Path(p).read_text(encoding='utf-8-sig'), catalog) for p in dict.fromkeys(files)]
        allowed = data.get('allowedConstructions', sorted({n for s in stories for n in s.constructions}))
        if not isinstance(allowed, list) or not allowed or not all(isinstance(n, str) for n in allowed) or len(set(allowed)) != len(allowed):
            raise ValueError('allowedConstructions должен быть непустым списком без повторов')
        for name in allowed:
            catalog.construction(name)
        if not isinstance(data.get('growth', {}), dict) or not isinstance(data.get('search', {}), dict):
            raise ValueError('growth и search должны быть JSON-объектами')
        growth = dict(data.get('growth', {})); search = dict(data.get('search', {}))
        if set(growth) - {'maxAddedDefinitions', 'variantsPerStory'} or set(search) - {'maxDepth', 'maxJobs', 'sliceSeconds', 'maxPaletteSize'}:
            raise ValueError('Неизвестный параметр growth/search')
        for key, default, high in [('maxAddedDefinitions', 1, 5), ('variantsPerStory', 12, 1000)]:
            growth[key] = integer(growth.get(key, default), 'growth.' + key, 0 if key == 'maxAddedDefinitions' else 1, high)
        for key, default, high in [('maxDepth', max(s.iterations for s in stories) + 1, 12), ('maxJobs', max(32, len(stories)), 100000), ('sliceSeconds', 30, 3600), ('maxPaletteSize', 5, 72)]:
            search[key] = integer(search.get(key, default), 'search.' + key, 1, high)
        if growth['maxAddedDefinitions'] >= search['maxDepth']:
            raise ValueError('maxAddedDefinitions должен быть меньше maxDepth')
        if not isinstance(data.get('includeRelaxations', True), bool):
            raise ValueError('includeRelaxations должен быть bool')
        for story in stories:
            if set(story.constructions) - set(allowed):
                raise ValueError('Построения исходного story выходят за allowedConstructions')
        config = dict(data, allowedConstructions=allowed, growth=growth, search=search,
                      seed=integer(data.get('seed', 1), 'seed', 0, 2**31-1), includeRelaxations=data.get('includeRelaxations', True))
        return cls(config, stories, catalog)

    def plans(self):
        """Finite reproducible exploration bank, originals plus explicitly linked variants."""
        rng = random.Random(self.config['seed']); plans = []; by_key = {}
        for source, original in enumerate(self.stories):
            seeds = [(original, 0, None)]
            relaxed = LAYOUT_RELAXATIONS.get(original.layout)
            if relaxed and self.config['includeRelaxations']:
                seeds.append((replace(original, layout=relaxed), 0, original.layout))
            for base, _, relaxation in list(seeds):
                frontier = [base]
                for added in range(1, self.config['growth']['maxAddedDefinitions'] + 1):
                    next_frontier = []
                    for parent in frontier:
                        types = parent.validate(self.catalog)
                        available = {t: [n for n, typ in types.items() if typ == t and n not in parent.hidden] for t in set(types.values())}
                        candidates = list(self.config['allowedConstructions']); rng.shuffle(candidates)
                        new_objects = set()
                        for op in candidates:
                            sig = self.catalog.construction(op)
                            if any(len(available.get(t, [])) < sig['arguments'].count(t) for t in set(sig['arguments'])):
                                continue
                            for _ in range(4):
                                remaining = {t: list(v) for t, v in available.items()}; args = []
                                for typ in sig['arguments']:
                                    item = rng.choice(remaining[typ]); remaining[typ].remove(item); args.append(item)
                                # These identities follow from the user's right-angle hypothesis.
                                if parent.layout == 'RightTriangle' and set(args) == set(parent.names):
                                    if op == 'Orthocenter' or (op == 'PerpendicularProjectionOnLineFromPoints' and args[0] != parent.names[0]):
                                        continue
                                key = (op, self.catalog.arguments_key(op, args))
                                if key in new_objects or any(op == o and self.catalog.arguments_key(op, a) == key[1] for _, o, a in parent.definitions):
                                    continue
                                new_objects.add(key)
                                name = next(f'V{i}' for i in range(len(types) + 2) if f'V{i}' not in types)
                                child = replace(parent, definitions=parent.definitions + ((name, op, tuple(args)),))
                                child.validate(self.catalog)
                                from standard_reasoning import context
                                try:
                                    affine = context(child.configuration()).points
                                    if name in affine and any(value == affine[name] for old, value in affine.items() if old != name):
                                        continue
                                except (ValueError, KeyError):
                                    pass
                                next_frontier.append(child); seeds.append((child, added, relaxation))
                                if len(next_frontier) >= self.config['growth']['variantsPerStory']:
                                    break
                            if len(next_frontier) >= self.config['growth']['variantsPerStory']:
                                break
                        if len(next_frontier) >= self.config['growth']['variantsPerStory']:
                            break
                    frontier = next_frontier
            for story, added, relaxation in seeds:
                # Every palette is a subset of the user's explicit family. Include the original palette first.
                palettes = [story.constructions]
                pool = list(self.config['allowedConstructions']); rng.shuffle(pool)
                palettes.append(tuple(pool[:self.config['search']['maxPaletteSize']]))
                for palette_index, palette in enumerate(palettes):
                    prefix_types = [self.catalog.construction(op)['output'] for _, op, _ in story.definitions[len(original.definitions):]]
                    limits = tuple(limit - prefix_types.count(typ) for limit, typ in zip(original.limits, ('Point', 'Line', 'Circle')))
                    if min(limits) < 0 or not any(limits):
                        continue
                    depth = min(story.iterations, self.config['search']['maxDepth'] - added)
                    if depth < 1:
                        continue
                    task = replace(story, constructions=palette, iterations=depth, limits=limits)
                    key = digest(task.text())
                    origin = dict(source=source, addedDefinitions=added, relaxedFrom=relaxation)
                    if key in by_key:
                        by_key[key]['seedOrigins'].append(origin)
                        continue
                    plans.append({'story': task.text(), 'source': source, 'addedDefinitions': added,
                                  'relaxedFrom': relaxation, 'key': key, 'palette': list(palette), 'paletteIndex': palette_index, 'extensionFamily': story.definitions[-1][1] if added else None, 'seedOrigins': [origin]})
                    by_key[key] = plans[-1]
        # Fairly interleave sources and original/relaxed/expanded variants.
        plans.sort(key=lambda p: (p['addedDefinitions'], bool(p['relaxedFrom']), p['paletteIndex'], p['source']))
        return plans[:self.config['search']['maxJobs']]
