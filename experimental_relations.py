"""Experimental numerical relation mining. No theorem database or prover.

The score measures short joint witnesses in a bounded grammar, not difficulty.
Uses only the standard library; all macro definitions come from GeoGen's catalog.
"""
from __future__ import annotations
from dataclasses import replace
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import time
import sys

from families import parse_configuration, digest


class Unsupported(ValueError):
    pass


def sub(a,b): return (a[0]-b[0],a[1]-b[1])
def add(a,b): return (a[0]+b[0],a[1]+b[1])
def mul(a,k): return (a[0]*k,a[1]*k)
def dot(a,b): return a[0]*b[0]+a[1]*b[1]
def cross(a,b): return a[0]*b[1]-a[1]*b[0]
def norm(a): return math.hypot(*a)


def line(a,b):
    d=sub(b,a);n=norm(d)
    if n<1e-9: raise ValueError('zero line')
    return (-d[1]/n,d[0]/n,cross(a,b)/n)


def intersection(a,b):
    d=a[0]*b[1]-a[1]*b[0]
    if abs(d)<1e-9: raise ValueError('parallel intersection')
    return ((a[1]*b[2]-a[2]*b[1])/d,(a[2]*b[0]-a[0]*b[2])/d)


def circle(a,b,c):
    u,v=sub(b,a),sub(c,a);d=2*cross(u,v)
    if abs(d)<1e-9*max(1,norm(u)*norm(v)):raise ValueError('collinear circle')
    o=add(a,((dot(u,u)*v[1]-dot(v,v)*u[1])/d,
             (u[0]*dot(v,v)-v[0]*dot(u,u))/d))
    return (o[0],o[1],dot(sub(a,o),sub(a,o)))


def primitive(op,a):
    if op=='Midpoint':return mul(add(a[0],a[1]),.5)
    if op=='PointReflection':return sub(mul(a[1],2),a[0])
    if op=='LineFromPoints':return line(*a)
    if op=='Circumcircle':return circle(*a)
    if op=='CenterOfCircle':return a[0][:2]
    if op=='CircleWithCenterThroughPoint':
        d=sub(a[1],a[0]);return (*a[0],dot(d,d))
    if op=='IntersectionOfLines':return intersection(*a)
    if op=='PerpendicularProjection':
        p,l=a;t=(l[0]*p[0]+l[1]*p[1]+l[2])/(l[0]*l[0]+l[1]*l[1])
        return sub(p,mul(l[:2],t))
    if op=='ParallelLine':
        p,l=a;return (l[0],l[1],-l[0]*p[0]-l[1]*p[1])
    if op=='PerpendicularLine':
        p,l=a;return (-l[1],l[0],l[1]*p[0]-l[0]*p[1])
    if op=='InternalAngleBisector':
        p,b,c=a;u,v=sub(b,p),sub(c,p)
        return line(p,add(p,add(mul(u,1/norm(u)),mul(v,1/norm(v)))))
    if op=='InversionOfPoint':
        p,c=a;d=sub(p,c[:2]);return add(c[:2],mul(d,c[2]/dot(d,d)))
    if op in ('CenterOfPositiveHomothety','CenterOfNegativeHomothety'):
        c,d=a;r,s=math.sqrt(c[2]),math.sqrt(d[2]);sign=-1 if op=='CenterOfPositiveHomothety' else 1
        if abs(r+sign*s)<1e-9:raise ValueError('homothety at infinity')
        return mul(add(mul(d[:2],r),mul(c[:2],sign*s)),1/(r+sign*s))
    if op in ('SecondIntersectionOfCircleAndLineFromPoints','SecondIntersectionOfTwoCircumcircles'):
        p=a[0]
        if len(a)==4:
            c=circle(p,a[2],a[3]);direction=sub(a[1],p)
        else:
            c=circle(p,a[1],a[2]);d=circle(p,a[3],a[4]);direction=(-(d[1]-c[1]),d[0]-c[0])
        length=dot(direction,direction)
        if length<1e-14:raise ValueError('coincident circles/line')
        t=-2*dot(direction,sub(p,c[:2]))/length
        if abs(t)*math.sqrt(length)<1e-8:raise ValueError('tangent second intersection')
        return add(p,mul(direction,t))
    raise Unsupported('Unsupported primitive: '+op)


class Geometry:
    """Hash-consed primitive DAG, evaluated on independent moving base pictures."""
    def __init__(self,catalog,layout='Triangle',names=('A','B','C'),seed=17,samples=12):
        if layout not in ('Triangle','RightTriangle'):raise Unsupported('Experimental bases: Triangle, RightTriangle')
        self.catalog=catalog;self.nodes=[];self.index={};self.values={};self.cost={};self.compile_cache={};self.expressions={}
        self.names=names;self.samples=samples;self.attempts=samples*3;self.seed=seed
        rng=random.Random(seed)
        self.worlds=[]
        for _ in range(self.attempts):
            u=0 if layout=='RightTriangle' else rng.uniform(-.45,1.45)
            self.worlds.append(((0.,0.),(1.,0.),(u,rng.uniform(.25,1.8))))
        self.roots={name:self.intern('root',(), 'Point',extra=i) for i,name in enumerate(names)}

    def intern(self,op,args,typ,extra=None):
        key=(op,tuple(args),extra)
        if key not in self.index:
            if len(self.nodes)>=20000:raise Unsupported('Experimental DAG budget exceeded')
            self.index[key]=len(self.nodes);self.nodes.append((op,tuple(args),typ,extra))
        return self.index[key]

    def canonical(self,op,args):
        it=iter(args)
        def group(parameter):
            if 'type' in parameter:return [next(it)]
            children=[group(parameter['item']) for _ in range(parameter['count'])]
            return [v for child in sorted(children) for v in child]
        return tuple(v for p in self.catalog.construction(op)['parameters'] for v in group(p))

    def compile(self,op,args):
        args=self.canonical(op,args);key=(op,args)
        if key in self.compile_cache:return self.compile_cache[key]
        spec=self.catalog.construction(op)
        if spec.get('definition'):
            _,names,definitions=parse_configuration(spec['definition']);env=dict(zip(names,args))
            for name,operation,arguments in definitions:
                env[name]=self.compile(operation,tuple(env[x] for x in arguments))
            result=env[definitions[-1][0]]
        else:result=self.intern(op,args,spec['output'])
        self.compile_cache[key]=result
        self.expressions.setdefault(result,[]).append((op,args))
        return result

    def story(self,story):
        env=dict(self.roots)
        for name,op,args in story.definitions:env[name]=self.compile(op,tuple(env[x] for x in args))
        return env

    def at(self,node,world):
        key=(node,world)
        if key in self.values:
            if self.values[key] is None:raise ValueError('undefined geometry')
            return self.values[key]
        op,args,_,extra=self.nodes[node]
        try:
            value=self.worlds[world][extra] if op=='root' else primitive(op,[self.at(n,world) for n in args])
            if not all(math.isfinite(x) and abs(x)<1e7 for x in value):raise ValueError('nonfinite geometry')
        except (ValueError,ZeroDivisionError,OverflowError):value=None
        self.values[key]=value
        if value is None:raise ValueError('undefined geometry')
        return value

    def ancestors(self,nodes):
        result=set();pending=list(nodes)
        while pending:
            node=pending.pop()
            if node not in result:result.add(node);pending.extend(self.nodes[node][1])
        return result

    def compatible_worlds(self,nodes):
        result=[]
        for w in range(self.attempts):
            try:
                for n in nodes:self.at(n,w)
            except (ValueError,TypeError):continue
            result.append(w)
            if len(result)==self.samples:return result
        raise ValueError('Not enough nondegenerate independent pictures')


class Miner:
    """Search a bounded grammar of short *joint* witnesses, not named theorems."""
    def __init__(self,geometry,helper_budget=8,anchor_budget=16,predicate_budget=640):
        self.g=geometry;self.helper_budget=helper_budget;self.anchor_budget=anchor_budget
        self.predicate_budget=predicate_budget;self.cache={}

    def pair(self,p,q,context=()):
        key=(min(p,q),max(p,q),tuple(sorted(context)))
        if key in self.cache:return self.cache[key]
        began=time.perf_counter();g=self.g
        if g.nodes[p][2]!='Point' or g.nodes[q][2]!='Point':raise Unsupported('Point-pair profile only')
        # An auxiliary object defined *from the queried outputs* cannot explain them.
        context=tuple(n for n in context if not ({p,q}&g.ancestors((n,))))
        ancestors=g.ancestors((p,q,*context));worlds=g.compatible_worlds(ancestors)
        if any(norm(sub(g.at(p,w),g.at(q,w)))<1e-7 for w in worlds):raise ValueError('coincident target points')
        # A verbose wrapper may reproduce a simpler intermediate point. Recognize
        # this numerically for the heuristic only; never rewrite the theorem/input.
        representatives=[]
        for target in (p,q):
            candidates=[n for n in g.ancestors((target,)) if g.nodes[n][2]=='Point']
            candidates.sort(key=lambda n:(len(g.ancestors((n,))),n))
            representative=target
            for n in candidates[:32]:
                if all(norm(sub(g.at(n,w),g.at(target,w)))<1e-8*max(1,norm(g.at(target,w))) for w in worlds):
                    representative=n;break
            representatives.append(representative)
        p,q=representatives;ancestors=g.ancestors((p,q,*context))
        points=list(dict.fromkeys([n for n in context if g.nodes[n][2]=='Point']+
                                  [n for n in sorted(ancestors) if g.nodes[n][2]=='Point' and n not in (p,q)]))
        distance={p:0,q:0};pending=[p,q]
        for n in pending:
            for parent in g.nodes[n][1]:
                if parent not in distance:distance[parent]=distance[n]+1;pending.append(parent)
        lines=sorted((n for n in ancestors if g.nodes[n][2]=='Line'),key=lambda n:(distance.get(n,100),n))
        # Helpers only intersect already present carrier lines. No target-made perpendiculars.
        helpers=[];helper_attempts=0
        for a,b in itertools.combinations(lines,2):
            if len(helpers)>=self.helper_budget or helper_attempts>=64:break
            helper_attempts+=1
            n=g.intern('IntersectionOfLines',(a,b),'Point')
            try:
                vals=[g.at(n,w) for w in worlds]
                if any(any(norm(sub(value,g.at(t,w)))<1e-7 for w,value in zip(worlds,vals)) for t in (p,q)):continue
                if any(all(norm(sub(value,g.at(old,w)))<1e-7 for w,value in zip(worlds,vals)) for old in points+helpers):continue
            except (ValueError,TypeError):continue
            helpers.append(n)
        anchors=[]
        for n in points+helpers:
            try:
                if any(all(norm(sub(g.at(n,w),g.at(old,w)))<1e-7 for w in worlds) for old in anchors+[p,q]):continue
            except (ValueError,TypeError):continue
            anchors.append(n)
        # Preserve room for virtual helpers; deterministic caps are explicit in the report.
        anchors=anchors[:max(0,self.anchor_budget-len(helpers))]+[h for h in helpers if h not in anchors[:max(0,self.anchor_budget-len(helpers))]]
        witnesses=[];tested=0
        transformation_probes=0;root_nodes=set(g.roots.values())
        # Discover a short inverse law from the operation itself, without a registry
        # saying which named constructions are involutions. Only a small independent
        # background frame is allowed; an arbitrary target-made frame is not free.
        for source,target in ((p,q),(q,p)):
            for operation,args in list(g.expressions.get(target,[])):
                positions=[i for i,n in enumerate(args) if n==source]
                if len(positions)!=1 or transformation_probes>=2:continue
                frame=[n for i,n in enumerate(args) if i!=positions[0]]
                if any(({p,q}&g.ancestors((n,))) or len(g.ancestors((n,))-root_nodes)>3 for n in frame):continue
                transformation_probes+=1
                changed=list(args);changed[positions[0]]=target
                try:
                    back=g.compile(operation,tuple(changed))
                    errors=[norm(sub(g.at(back,w),g.at(source,w)))/max(1,norm(g.at(source,w))) for w in worlds]
                    if max(errors)<1e-8:
                        witnesses.append(dict(kind='involution',anchors=frame,helperCount=1,
                                              operation=operation,maxResidual=max(errors)))
                except (ValueError,TypeError):pass
        def accept(kind,ns,fn,**metadata):
            nonlocal tested
            if tested>=self.predicate_budget:return
            tested+=1
            try:
                residuals=[]
                for w in worlds:
                    value=fn(w)
                    if abs(value)>=1e-8:return
                    residuals.append(value)
                witnesses.append(dict(kind=kind,anchors=list(ns),helperCount=sum(n in helpers for n in ns),maxResidual=max(abs(x) for x in residuals),**metadata))
            except (ValueError,ZeroDivisionError,TypeError):pass
        for a in anchors:
            def collinear(w,a=a):
                u,v=sub(g.at(p,w),g.at(a,w)),sub(g.at(q,w),g.at(a,w))
                return cross(u,v)/max(norm(u)*norm(v),1e-12)
            accept('collinear',(a,),collinear)
        # Shared circles precede optional equal-length witnesses to keep a bounded search.
        for a,b in itertools.combinations(anchors,2):
            def cyclic(w,a=a,b=b):
                origin=g.at(p,w);u,v,z=(sub(g.at(n,w),origin) for n in (a,b,q))
                if min(norm(u),norm(v),norm(z),norm(sub(u,v)),norm(sub(u,z)),norm(sub(v,z)))<1e-8:raise ValueError('coincident circle witness')
                if abs(cross(u,v))<1e-6*norm(u)*norm(v):raise ValueError('ill-conditioned circle')
                scale=max(norm(u),norm(v),norm(z))
                return (cross(u,v)*dot(z,z)-cross(z,v)*dot(u,u)-cross(u,z)*dot(v,v))/scale**4
            accept('shared-circle',(a,b),cyclic)
        for kind in ('parallel','perpendicular','squared-length-ratio'):
            for a,b in itertools.combinations(anchors,2):
                coefficient=1.
                if kind=='squared-length-ratio':
                    u,v=sub(g.at(p,worlds[0]),g.at(q,worlds[0])),sub(g.at(a,worlds[0]),g.at(b,worlds[0]))
                    measured=dot(u,u)/max(dot(v,v),1e-20)
                    coefficient=min({i/j for i in range(1,5) for j in range(1,5)},key=lambda c:abs(c-measured))
                def relation(w,a=a,b=b,kind=kind,coefficient=coefficient):
                    u,v=sub(g.at(p,w),g.at(q,w)),sub(g.at(a,w),g.at(b,w))
                    if kind=='squared-length-ratio':return (dot(u,u)-coefficient*dot(v,v))/max(1e-20,dot(u,u),coefficient*dot(v,v))
                    denominator=norm(u)*norm(v)
                    if denominator<1e-10:raise ValueError('zero relation line')
                    return (cross(u,v) if kind=='parallel' else dot(u,v))/denominator
                accept(kind,(a,b),relation,**({'coefficient':coefficient} if kind=='squared-length-ratio' else {}))
        for a,b in itertools.product(anchors,repeat=2):
            def equal(w,a=a,b=b):
                u,v=sub(g.at(p,w),g.at(a,w)),sub(g.at(q,w),g.at(b,w))
                return (dot(u,u)-dot(v,v))/max(1e-20,dot(u,u),dot(v,v))
            accept('equal-segments',(a,b),equal)
        # Saturate per witness family: many redundant equalities cannot dominate the score.
        best={}
        weights={'collinear':1.,'shared-circle':1.,'parallel':.75,'perpendicular':.75,
                 'squared-length-ratio':.25,'equal-segments':.25,'involution':2.}
        for witness in witnesses:
            cost=1+len(set(witness['anchors']))+witness['helperCount']
            best[witness['kind']]=max(best.get(witness['kind'],0),weights[witness['kind']]/cost)
        strength=sum(best.values())
        result=dict(version=1,strength=round(strength,6),families=best,witnesses=witnesses,
                    tested=tested,transformationProbes=transformation_probes,anchors=len(anchors),helpers=len(helpers),helperAttempts=helper_attempts,samples=len(worlds),
                    status='joint-witness-found' if witnesses else 'no-witness-within-budget',
                    numericalRepresentatives=representatives,
                    elapsedSeconds=time.perf_counter()-began,
                    notice='Bounded numerical joint regularity, not human difficulty or proof.')
        self.cache[key]=result
        return result


def analyze(story,catalog,pair=None,seed=17,samples=12):
    g=Geometry(catalog,story.layout,story.names,seed,samples);env=g.story(story)
    if pair is None:
        pair=[name for name in env if name not in story.names and g.nodes[env[name]][2]=='Point'][-2:]
    if len(pair)!=2 or any(name not in env for name in pair):raise ValueError('Select two existing point names')
    report=Miner(g).pair(*(env[n] for n in pair))
    aliases={v:k for k,v in env.items()}
    def description(node,depth=0):
        if node in aliases:return aliases[node]
        if depth>5:return '#'+str(node)
        options=[]
        for (op,args),result in g.compile_cache.items():
            if result==node:options.append((op,args))
        op,args,_,extra=g.nodes[node]
        if op=='root':return story.names[extra]
        options.append((op,args))
        return min((op+'('+', '.join(description(a,depth+1) for a in args)+')' for op,args in options),key=len)
    report=dict(report,pair=list(pair),configuration=story.configuration(),seed=seed,
                anchorDescriptions={str(n):description(n) for w in report['witnesses'] for n in w['anchors']})
    return report


def analyze_configuration(story,catalog,*,seed=17,samples=12,pair_budget=24):
    """Expose shared carriers, including ones absent from the user's definitions."""
    if not 1<=pair_budget<=64:raise ValueError('pair-budget: 1..64')
    g=Geometry(catalog,story.layout,story.names,seed,samples);env=g.story(story);miner=Miner(g)
    points=[n for n,node in env.items() if n not in story.names and g.nodes[node][2]=='Point']
    pairs=list(itertools.combinations(points,2));total=len(pairs)
    if len(pairs)>pair_budget:random.Random(seed).shuffle(pairs)
    rows=[];carriers={}
    for a,b in pairs[:pair_budget]:
        try:report=miner.pair(env[a],env[b],context=tuple(env[n] for n in points if n not in (a,b)))
        except ValueError as error:
            rows.append(dict(pair=[a,b],status='inconclusive',message=str(error)));continue
        rows.append(dict(pair=[a,b],**report))
        worlds=g.compatible_worlds(g.ancestors((env[a],env[b])))
        for witness in report['witnesses']:
            kind=witness['kind'];anchors=witness['anchors']
            if kind not in ('shared-circle','collinear'):continue
            values=[]
            for w in worlds:
                if kind=='shared-circle':value=circle(g.at(env[a],w),g.at(anchors[0],w),g.at(anchors[1],w))
                else:
                    value=line(g.at(env[a],w),g.at(env[b],w))
                    if value[0]<0 or (abs(value[0])<1e-12 and value[1]<0):value=tuple(-x for x in value)
                values.append(value)
            fingerprint=(kind,tuple(worlds),tuple(round(x,6) for value in values[:3] for x in value))
            group=carriers.setdefault(fingerprint,dict(kind=kind,members=set(),values=values))
            # The coarse signature is an index only: verify every sampled picture.
            if max(abs(x-y)/max(1,abs(x),abs(y)) for u,v in zip(values,group['values']) for x,y in zip(u,v))<1e-7:
                group['members'].update((a,b))
    return dict(version=1,experimental=True,configuration=story.configuration(),seed=seed,
                examinedPairs=len(rows),totalPairs=total,pairBudget=pair_budget,relations=rows,
                groups=[dict(kind=c['kind'],members=sorted(c['members'])) for c in carriers.values()],
                scope='Bounded measured joint regularities; absence is unknown, not human independence.')


def propose_actions(parent,catalog,g,env,miner,rng,proposal_budget=24,measure=True):
    operations=[op for op in parent.constructions if catalog.construction(op)['output']=='Point' and
                all(t=='Point' for t in catalog.construction(op)['arguments'])]
    if not operations:return [],0
    permutations=itertools.permutations(parent.names) if parent.layout=='Triangle' else (
        parent.names,(parent.names[0],parent.names[2],parent.names[1]))
    representations=[]
    for permutation in permutations:
        objects={n:('root',i) for i,n in enumerate(permutation)}
        for n,op,args in parent.definitions:objects[n]=(op,catalog.arguments_key(op,[objects[a] for a in args]))
        representations.append(objects)
    reference=representations[0]
    signature=lambda objects: (sorted(map(repr,objects.values())),sorted(repr(objects[n]) for n in parent.hidden))
    stabilizers=[objects for objects in representations if signature(objects)==signature(reference)]
    available=[n for n,node in env.items() if n not in parent.hidden and g.nodes[node][2]=='Point']
    proposals=[];seen=set();drawn=0;attempts=0
    while len(proposals)<proposal_budget and attempts<proposal_budget*8:
        attempts+=1;op=rng.choice(operations);arity=len(catalog.construction(op)['arguments'])
        if len(available)<arity:continue
        args=tuple(rng.sample(available,arity));key=(op,catalog.arguments_key(op,args))
        symmetry_key=min(repr((op,catalog.arguments_key(op,[objects[a] for a in args]))) for objects in stabilizers)
        if symmetry_key in seen:continue
        seen.add(symmetry_key)
        if any(op==o and catalog.arguments_key(op,a)==key[1] for _,o,a in parent.definitions):continue
        pairs=list(itertools.combinations(args,2));rng.shuffle(pairs);scores=[]
        if not measure:
            proposals.append(dict(operation=op,arguments=args,strength=None));continue
        for a,b in pairs[:3]:
            if a in parent.names and b in parent.names:scores.append(1.)
            else:
                try:
                    r=miner.pair(env[a],env[b],context=tuple(env[n] for n in args if n not in (a,b)));scores.append(min(1,2*r['strength']))
                except (ValueError,TypeError):scores=[];break
        if not scores:continue
        strength=sum(scores)/len(scores)
        proposals.append(dict(operation=op,arguments=args,strength=strength))
    return proposals,attempts


def sample_prefix(story,catalog,*,steps=1,proposal_budget=24,target=.2,seed=17,samples=12,max_depth=None):
    """Select arguments before drawing children. Restricted experimental point sampler.

    Returned stories are ordinary GeoGen inputs. This steers the prefix only;
    the subsequent legacy GeoGen search is unchanged.
    """
    if not 1<=steps<=5 or not 1<=proposal_budget<=64 or not 0<=target<=1:raise ValueError('Invalid sampling limits')
    if steps>story.limits[0]:raise ValueError('steps exceeds source MaximalPoints')
    max_depth=story.iterations if max_depth is None else max_depth
    if max_depth<=steps:raise ValueError('max-depth must leave at least one GeoGen iteration')
    g=Geometry(catalog,story.layout,story.names,seed,samples);env=g.story(story);miner=Miner(g)
    rng=random.Random(seed);parent=story;trace=[];added=0
    operations=[op for op in story.constructions if catalog.construction(op)['output']=='Point' and
                all(t=='Point' for t in catalog.construction(op)['arguments'])]
    if not operations:raise Unsupported('Experimental sampler requires point constructions with point arguments')
    for step in range(steps):
        proposals,attempts=propose_actions(parent,catalog,g,env,miner,rng,proposal_budget)
        drawn=0
        if not proposals:break
        # Rank only the bounded proposal batch, not all combinations of objects.
        proposals.sort(key=lambda p:abs(p['strength']-target))
        selected=None
        for proposal in proposals:
            drawn+=1
            try:
                node=g.compile(proposal['operation'],tuple(env[a] for a in proposal['arguments']))
                worlds=g.compatible_worlds(g.ancestors(tuple(env.values())+(node,)))
                if any(any(norm(sub(g.at(node,w),g.at(old,w)))<1e-7 for w in worlds) for old in env.values() if g.nodes[old][2]=='Point'):continue
            except (ValueError,TypeError):continue
            name=next('X'+str(i) for i in range(len(env)+1) if 'X'+str(i) not in env)
            parent=replace(parent,definitions=parent.definitions+((name,proposal['operation'],proposal['arguments']),))
            parent.validate(catalog);env[name]=node;selected=dict(proposal,name=name);added+=1
            break
        trace.append(dict(step=step,proposed=len(proposals),attemptedArguments=attempts,drawn=drawn,
                          selected=selected,target=target))
        if selected is None:break
    if not added:raise ValueError('No valid sampled extensions')
    if added>story.limits[0]:raise ValueError('Sampled prefix exceeds source MaximalPoints')
    result=replace(parent,iterations=min(story.iterations,max_depth-added),
                   limits=(story.limits[0]-added,*story.limits[1:]))
    return result,dict(version=1,experimental=True,seed=seed,target=target,trace=trace,
                       sourceKey=digest(story.text()),addedDefinitions=added,
                       scope='Point-only prefix selection; legacy GeoGen DFS is unchanged.',
                       notice='No-witness is unknown, not proof of weak human connection.')


def configure_worker(root,*,target=.2,proposals=24,keep=6,seed=17,policy='joint'):
    """Enable only this explicitly requested producer process and its children."""
    if not math.isfinite(target) or not 0<=target<=1 or not 1<=keep<=proposals<=64 or policy not in ('joint','uniform'):
        raise ValueError('Invalid experimental sampler settings')
    root=Path(root)
    try:marker=json.loads((root/'data/experimental-relations.json').read_text())
    except FileNotFoundError:raise ValueError('Пересоберите комплект: бинарник не поддерживает экспериментальный sampler')
    dll=root/'lib/geogen/GeoGen.ConfigurationGenerator.dll'
    if marker.get('version')!=1 or marker.get('sha256')!=hashlib.sha256(dll.read_bytes()).hexdigest():
        raise ValueError('Experimental sampler marker does not match the generator DLL')
    os.environ.update(GEOGEN_RELATION_WORKER=str(root/'experimental_relations.py'),
                      GEOGEN_RELATION_PYTHON=sys.executable,GEOGEN_RELATION_CATALOG=str(root/'data/construction-catalog.json'),
                      GEOGEN_RELATION_TARGET=str(target),GEOGEN_RELATION_PROPOSALS=str(proposals),
                      GEOGEN_RELATION_KEEP=str(keep),GEOGEN_RELATION_SEED=str(seed),GEOGEN_RELATION_POLICY=policy)


def worker_main():
    from families import Catalog,Story
    catalog=Catalog.read(os.environ['GEOGEN_RELATION_CATALOG'])
    target=float(os.environ.get('GEOGEN_RELATION_TARGET','.2'))
    budget=int(os.environ.get('GEOGEN_RELATION_PROPOSALS','24'))
    keep=int(os.environ.get('GEOGEN_RELATION_KEEP','6'))
    seed=int(os.environ.get('GEOGEN_RELATION_SEED','17'))
    policy=os.environ.get('GEOGEN_RELATION_POLICY','joint')
    if not math.isfinite(target) or not 0<=target<=1 or not 1<=keep<=budget<=64 or policy not in ('joint','uniform'):raise ValueError('Invalid worker limits')
    contexts={}
    for raw in sys.stdin:
        try:
            began=time.perf_counter();request=json.loads(raw)
            if request.get('version')!=1 or request.get('roots')!=3:raise Unsupported('Worker supports point triangle bases')
            layout=request['layout'];names=tuple('N'+str(i) for i in range(3))
            definitions=tuple(('N'+str(3+i),d['operation'],tuple('N'+str(a) for a in d['arguments'])) for i,d in enumerate(request['definitions']))
            story=Story(layout,names,definitions,tuple(request['operations']),iterations=1,
                        hidden=tuple('N'+str(i) for i in request.get('hidden',[])))
            story.validate(catalog)
            if layout not in contexts or len(contexts[layout][0].nodes)>2500 or len(contexts[layout][1].cache)>4096:
                g=Geometry(catalog,layout,names,seed);contexts[layout]=(g,Miner(g))
            g,miner=contexts[layout];env=g.story(story)
            rng=random.Random(int(digest([story.configuration(),seed])[:16],16))
            values_before=len(g.values)
            proposals,attempts=propose_actions(story,catalog,g,env,miner,rng,budget,measure=policy=='joint')
            fallback=False
            if not proposals:
                # Coverage failures retain a bounded random exploration path, never legacy n^k enumeration.
                fallback=True;seen=set();points=[n for n,node in env.items() if n not in story.hidden and g.nodes[node][2]=='Point']
                for _ in range(budget*8):
                    if len(proposals)>=budget:break
                    op=rng.choice(story.constructions);arity=len(catalog.construction(op)['arguments'])
                    if len(points)<arity:continue
                    args=tuple(rng.sample(points,arity));key=(op,catalog.arguments_key(op,args))
                    if key in seen:continue
                    seen.add(key);proposals.append(dict(operation=op,arguments=args,strength=None))
            proposals.sort(key=lambda p:abs(p['strength']-target) if p['strength'] is not None else 2)
            selected=[];remaining=list(proposals);used_operations=set()
            for position in range(keep):
                if not remaining:break
                # Preserve different construction families when equally promising root actions are symmetric.
                options=[p for p in remaining if p['operation'] not in used_operations] if position%3==1 else remaining
                item=(options or remaining)[0];remaining.remove(item);selected.append(item);used_operations.add(item['operation'])
            actions=[dict(operation=p['operation'],arguments=[int(n[1:]) for n in p['arguments']]) for p in selected]
            response=dict(actions=actions,statistics=dict(proposed=len(proposals),kept=len(actions),
                          attemptedArguments=attempts,definitions=len(definitions),target=target,policy=policy,
                          selectedStrengths=[p['strength'] for p in selected],fallback=fallback,
                          configuration=story.configuration(),selectedActions=actions,
                          primitiveEvaluations=len(g.values)-values_before,
                          elapsedMs=round(1000*(time.perf_counter()-began),3)))
        except Exception as error:response=dict(error=type(error).__name__+': '+str(error))
        print(json.dumps(response),flush=True)


if __name__=='__main__' and sys.argv[1:]==['--worker']:
    worker_main()
