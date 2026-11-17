"""Exact, deliberately partial standard-proof filters. No numerical facts are premises."""
from __future__ import annotations
from fractions import Fraction as Q
from functools import lru_cache
import itertools
import json
import re

from families import digest, parse_configuration

VERSION = 1


def add(a, b, factor=1):
    result = dict(a)
    for key, value in b.items():
        result[key] = result.get(key, 0) + factor * value
        if not result[key]:
            del result[key]
    return result


def scale(a, factor):
    return {k: v * factor for k, v in a.items() if v * factor}


class ModularSystem:
    """Integer row operations over R/Z; never divide a modular equation by a pivot."""
    def __init__(self, relations):
        self.relations = relations
        self.rows = []
        matrix = [[dict(a), Q(b) % 1, {i: 1}] for i, (a, b, _) in enumerate(relations)]
        columns = sorted({k for a, _, _ in matrix for k in a})
        rank, steps = 0, 0
        for column in columns:
            pivot = next((i for i in range(rank, len(matrix)) if matrix[i][0].get(column)), None)
            if pivot is None:
                continue
            matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
            for i in range(rank + 1, len(matrix)):
                while matrix[i][0].get(column):
                    steps += 1
                    if steps > 20000:
                        raise ValueError('modular-budget')
                    divisor = matrix[rank][0][column]
                    factor = matrix[i][0][column] // divisor
                    for field in (0, 2):
                        matrix[i][field] = add(matrix[i][field], matrix[rank][field], -factor)
                    matrix[i][1] = (matrix[i][1] - factor * matrix[rank][1]) % 1
                    if matrix[i][0].get(column):
                        matrix[rank], matrix[i] = matrix[i], matrix[rank]
            if matrix[rank][0][column] < 0:
                matrix[rank] = [scale(matrix[rank][0], -1), -matrix[rank][1] % 1, scale(matrix[rank][2], -1)]
            if any(abs(v).bit_length() > 512 for v in matrix[rank][0].values()):
                raise ValueError('modular-budget')
            self.rows.append((column, matrix[rank]))
            rank += 1
        self.inconsistent = any(not a and b % 1 for a, b, _ in matrix)

    def prove(self, coefficients, rhs):
        if self.inconsistent:
            return None
        residual, constant, proof = dict(coefficients), Q(rhs) % 1, {}
        for column, (row, value, origin) in self.rows:
            coefficient = residual.get(column, 0)
            pivot = row[column]
            if coefficient % pivot:
                return None
            factor = coefficient // pivot
            residual = add(residual, row, -factor)
            constant = (constant - factor * value) % 1
            proof = add(proof, origin, factor)
        return proof if not residual and constant == 0 else None

    def verify(self, coefficients, rhs, combination):
        row, value = {}, Q(0)
        for index, coefficient in combination.items():
            if not isinstance(coefficient, int) or not 0 <= index < len(self.relations):
                return False
            a, b, _ = self.relations[index]
            row = add(row, a, coefficient); value += coefficient * Q(b)
        return row == coefficients and (value - Q(rhs)) % 1 == 0


class Context:
    def __init__(self, configuration):
        self.configuration = configuration
        self.layout, names, self.definitions = parse_configuration(configuration)
        if len(self.definitions) > 80 or len(names) > 4:
            raise ValueError('configuration-budget')
        self.points = {name: {name: Q(1)} for name in names}
        self.lines = {}; self.known_pairs = set(); self.relations = []; self._relation_keys = set()
        self.noncollinear = set()
        self.affine_sources = {}
        self.circle_groups = {}
        # These point-only layouts guarantee distinct base points.
        if self.layout not in ('Triangle', 'RightTriangle', 'Quadrilateral', 'CyclicQuadrilateral', 'LineSegment'):
            raise ValueError('unsupported-layout')
        self.known_pairs.update(tuple(sorted(pair)) for pair in itertools.combinations(names, 2))
        for name, op, args in self.definitions:
            if op == 'LineFromPoints' and len(args) == 2:
                pair = tuple(sorted(args)); self.lines[name] = pair; self.known_pairs.add(pair)
        for name, op, args in self.definitions:
            if op == 'Circumcircle' and len(args) == 3:
                self.known_pairs.update(tuple(sorted(pair)) for pair in itertools.combinations(args, 2))
                self.noncollinear.add(frozenset(args))
        if self.layout in ('Triangle', 'RightTriangle', 'Quadrilateral', 'CyclicQuadrilateral'):
            self.noncollinear.update(frozenset(t) for t in itertools.combinations(names, 3))
        if self.layout == 'RightTriangle':
            self.relation([(self.line(names[0], names[1]), 1), (self.line(names[0], names[2]), -1)], Q(1, 2), 'initial:RightTriangle')
        if self.layout == 'CyclicQuadrilateral':
            for a, b, c, d in itertools.permutations(names):
                self.relation([(self.line(a, c), 1), (self.line(a, b), -1), (self.line(d, c), -1), (self.line(d, b), 1)], 0, 'initial:CyclicQuadrilateral')
        for name, op, args in self.definitions:
            self._definition(name, op, args)
            triples = []
            if op == 'SecondIntersectionOfCircleAndLineFromPoints' and len(args) == 4:
                triples = [(args[0], args[2], args[3])]
                self.known_pairs.add(tuple(sorted((args[0], name))))
            elif op == 'SecondIntersectionOfTwoCircumcircles' and len(args) == 5:
                triples = [(args[0], args[1], args[2]), (args[0], args[3], args[4])]
                self.known_pairs.add(tuple(sorted((args[0], name))))
            elif op in ('MidpointOfArc', 'MidpointOfOppositeArc') and len(args) == 3:
                triples = [args]
            for triple in triples:
                key = tuple(sorted(triple))
                group, sources = self.circle_groups.setdefault(key, (set(triple), set()))
                group.add(name); sources.add(f'{name}={op}({",".join(args)})')
                self.noncollinear.add(frozenset(triple))
                self.known_pairs.update(tuple(sorted(pair)) for pair in itertools.combinations(triple, 2))
        for group, sources in self.circle_groups.values():
            if len(group) > 8:
                raise ValueError('circle-budget')
            for a,b,c,d in itertools.permutations(sorted(group), 4):
                self.relation([(self.line(a,c),1),(self.line(a,b),-1),(self.line(d,c),-1),(self.line(d,b),1)],0,'; '.join(sorted(sources)))
        if len(self.relations) > 128:
            raise ValueError('relation-budget')
        self.system = ModularSystem(self.relations)

    def line(self, a, b):
        pair = tuple(sorted((a, b)))
        return 'pair:' + ','.join(pair) if pair in self.known_pairs else None

    def line_object(self, text):
        text = text.strip()
        if text.startswith('[') and text.endswith(']'):
            args = tuple(s.strip() for s in text[1:-1].split(','))
            return self.line(*args) if len(args) == 2 else None
        if text in self.lines:
            line = self.lines[text]
            return line if isinstance(line, str) else self.line(*line)
        return None

    def relation(self, terms, rhs, source):
        if any(line is None for line, _ in terms):
            return
        row = {}
        for line, coefficient in terms:
            row = add(row, {line: coefficient})
        key = (tuple(sorted(row.items())), Q(rhs) % 1)
        if key not in self._relation_keys:
            self._relation_keys.add(key)
            self.relations.append((row, Q(rhs), source))

    def _definition(self, name, op, args):
        source = f'{name}={op}({",".join(args)})'
        if op in ('PerpendicularLine', 'ParallelLine') and len(args) == 2:
            original = self.line_object(args[1]); self.lines[name] = 'object:' + name
            self.relation([(self.lines[name], 1), (original, -1)], Q(1, 2) if op == 'PerpendicularLine' else 0, source)
        elif op in ('InternalAngleBisector', 'ExternalAngleBisector') and len(args) == 3:
            a, b, c = args; self.lines[name] = 'object:' + name
            self.relation([(self.lines[name], 2), (self.line(a, b), -1), (self.line(a, c), -1)], 0, source)
        elif op in ('PerpendicularProjectionOnLineFromPoints', 'BisectorEnd', 'ExternalBisectorEnd') and len(args) == 3:
            a, b, c = args
            if op == 'PerpendicularProjectionOnLineFromPoints':
                self.relation([(self.line(a, name), 1), (self.line(b, c), -1)], Q(1, 2), source)
            else:
                self.relation([(self.line(a, name), 2), (self.line(a, b), -1), (self.line(a, c), -1)], 0, source)
        affine = None
        if all(a in self.points for a in args):
            values = [self.points[a] for a in args]
            if op == 'Midpoint' and len(values) == 2:
                affine = scale(add(values[0], values[1]), Q(1, 2))
            elif op == 'PointReflection' and len(values) == 2:
                affine = add(scale(values[1], 2), values[0], -1)
            elif op in ('Centroid', 'QuadrilateralCentroid') and len(values) == (3 if op == 'Centroid' else 4):
                affine = {}
                for value in values:
                    affine = add(affine, value, Q(1, len(values)))
        if affine is not None:
            self.points[name] = affine
            self.affine_sources[name] = source
        # Unknown point constructions deliberately remain unknown, never arbitrary affine variables.

    def vector(self, a, b):
        return add(self.points[b], self.points[a], -1)

    @staticmethod
    def proportional(a, b):
        if not a or not b:
            return Q(0) if not a else None
        key = next(iter(b)); factor = a.get(key, 0) / b[key]
        return factor if a == scale(b, factor) else None

    def prove(self, theorem):
        kind, text = theorem.split(':', 1); kind = kind.strip()
        args = re.findall(r'\[[^\]]+\]|\([^\)]+\)|\w+', text)
        row, rhs = None, Q(0)
        if kind == 'CollinearPoints' and len(args) == 3:
            ab, ac = self.line(args[0], args[1]), self.line(args[0], args[2])
            if ab and ac:
                row = add({ab: 1}, {ac: 1}, -1)
        elif kind == 'ConcyclicPoints' and len(args) == 4 and len(set(args)) == 4 and any(frozenset(t) in self.noncollinear for t in itertools.combinations(args, 3)):
            a,b,c = next(t for t in itertools.combinations(args, 3) if frozenset(t) in self.noncollinear)
            d = next(p for p in args if p not in (a,b,c))
            lines = [self.line(a,c),self.line(a,b),self.line(d,c),self.line(d,b)]
            if all(lines):
                row = {}
                for line, coefficient in zip(lines, (1,-1,-1,1)):
                    row = add(row,{line:coefficient})
        if row is not None:
            proof = self.system.prove(row, rhs)
            if proof is not None and self.system.verify(row, rhs, proof):
                sources = sorted({source for i in proof for source in self.relations[i][2].split('; ')})
                return {'status':'proved','method':'angles','cost':len(sources),
                        'certificate':{'version':VERSION,'configuration':digest(self.configuration),'goal':theorem,
                                       'combination':{str(i):v for i,v in proof.items()},'sources':sources}}
        if kind in ('ParallelLines', 'PerpendicularLines') and len(args) == 2:
            lines = [self.line_object(a) for a in args]
            if all(lines):
                row = add({lines[0]: 1}, {lines[1]: 1}, -1)
                rhs = Q(0) if kind == 'ParallelLines' else Q(1, 2)
                proof = self.system.prove(row, rhs)
                if proof is not None and self.system.verify(row, rhs, proof):
                    sources = sorted({source for i in proof for source in self.relations[i][2].split('; ')})
                    return {'status': 'proved', 'method': 'angles', 'cost': len(sources),
                            'certificate': {'version': VERSION, 'configuration': digest(self.configuration), 'goal': theorem,
                                            'combination': {str(i): v for i, v in proof.items()}, 'sources': sources}}
        try:
            identity = None
            if kind == 'CollinearPoints' and len(args) == 3:
                u, v = self.vector(args[0], args[1]), self.vector(args[0], args[2])
                factor = self.proportional(u, v)
                if factor is not None:
                    identity = {'factor': str(factor)}
            elif kind == 'EqualLineSegments' and len(args) == 4:
                u, v = self.vector(args[0], args[1]), self.vector(args[2], args[3])
                if u == v or u == scale(v, -1):
                    identity = {'sign': 1 if u == v else -1}
            elif kind == 'ParallelLines' and len(args) == 2 and all(a.startswith('[') for a in args):
                pairs = [tuple(s.strip() for s in a[1:-1].split(',')) for a in args]
                if all(len(p) == 2 and self.line(*p) for p in pairs):
                    factor = self.proportional(self.vector(*pairs[0]), self.vector(*pairs[1]))
                    if factor:
                        identity = {'factor': str(factor)}
            if identity is not None:
                sources = sorted(self.affine_sources.values())
                return {'status': 'proved', 'method': 'affine', 'cost': len(sources),
                        'certificate': {'version': VERSION, 'configuration': digest(self.configuration), 'goal': theorem,
                                        'identity': identity, 'sources': sources}}
        except KeyError:
            pass
        return {'status': 'unknown', 'method': None}


@lru_cache(maxsize=32)
def context(configuration):
    return Context(configuration)


def check(configuration, theorem):
    try:
        return context(configuration).prove(theorem)
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        return {'status': 'unknown', 'method': None}


def verify_certificate(configuration, theorem, result):
    """Rebuild all premises from definitions; never trust submitted equations as axioms."""
    if result.get('status') != 'proved':
        return False
    try:
        fresh = Context(configuration).prove(theorem)
        return fresh == result
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        return False
