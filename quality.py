"""Auditable quality checks: exact sample validation and structural diagnostics."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import collections
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from families import digest, parse_configuration
from standard_reasoning import check, verify_certificate

VERSION = 1


def structure(event):
    layout,names,definitions = parse_configuration(event['configuration'])
    objects = {name:(op,args) for name,op,args in definitions}
    kind,goal = event['theorem'].split(':',1)
    pending = list(re.findall(r'\w+',goal)); used=set(); base=set(); levels={n:0 for n in names}
    for name,op,args in definitions:
        levels[name]=1+max((levels.get(a,0) for a in args),default=0)
    while pending:
        name=pending.pop()
        if name in used:continue
        used.add(name)
        if name in objects:pending.extend(objects[name][1])
        elif name in names:base.add(name)
    unused=[n for n,_,_ in definitions if n not in used]
    compactness=1-len(unused)/max(1,len(definitions))
    ops=collections.Counter(op for _,op,_ in definitions)
    motifs=[]
    if all(ops[x] for x in ('Circumcenter','Orthocenter','Centroid')):motifs.append('triangle-centres/euler-candidate')
    if ops['PerpendicularProjectionOnLineFromPoints']>=3:motifs.append('pedal-configuration')
    if ops['IncircleTangentPoint']>=3:motifs.append('intouch-configuration')
    if ops['PonceletPoint'] and layout=='CyclicQuadrilateral':motifs.append('cyclic-Poncelet-configuration')
    if ops['MidpointOfArc']+ops['MidpointOfOppositeArc']>=2:motifs.append('arc-midpoint-configuration')
    return {'knownMotifs':motifs,'layout':layout,'topic':kind.strip(),'definitions':len(definitions),
            'essentialDefinitions':len(definitions)-len(unused),'unusedDefinitions':unused,
            'baseSupport':sorted(base),'depth':max((levels[n] for n in used if n in levels),default=0),
            'compactness':round(compactness,4)}


def inspect_event(event, extending, directory, *, samples=8, milliseconds=2000, triangle_domain="generic"):
    """No unknown result is promoted to sampled-valid or to a mathematical proof."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    features=structure(event)
    proof=check(event['configuration'],event['theorem'])
    report={'version':VERSION,'configurationKey':digest(event['configuration']),
            'theorem':event['theorem'],'features':features,'warnings':[],
            'standard':proof if proof['status']=='proved' else {'status':'unknown'}}
    if features['unusedDefinitions']:
        report['warnings'].append('unused-definitions')
    if proof['status']=='proved' and verify_certificate(event['configuration'],event['theorem'],proof) and proof['cost']<=12:
        report.update(verdict='standard',truth={'status':'proved-by-certificate'},qualityScore=0)
    else:
        check_file=directory/'quality-input.geo'
        text=event['configuration'].rstrip()+'\n'+event['theorem']+'\n\n\n\n\n'
        check_file.write_text(''.join('!'+line+'\n' for line in text.splitlines()))
        seed=int(digest([event['configuration'],event['theorem']])[:8],16)%2147483647
        began=time.monotonic()
        try:
            result=subprocess.run([str(extending),'--validate-file',str(check_file),str(milliseconds),str(samples),str(seed),triangle_domain],
                                  capture_output=True,text=True,timeout=milliseconds/1000+3,start_new_session=True)
            (directory/'quality-worker.log').write_text(result.stdout+result.stderr)
            if result.returncode:raise ValueError('Validation backend failed')
            truth=json.loads(result.stdout)
            if truth.get('status') not in ('sampled-valid','counterexample','inconclusive','error'):
                raise ValueError('Unsupported validation response; rebuild the suite')
            if truth['status']=='sampled-valid' and (truth.get('samples',0)<samples or truth.get('seed')!=seed):
                raise ValueError('Incomplete or mismatched sample report')
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            truth={'status':'inconclusive','message':str(error)}
        truth.setdefault('seed',seed)
        truth['requestedSamples']=samples
        truth['domain']=triangle_domain if features['layout']=='Triangle' else 'declared-layout'
        truth['elapsedSeconds']=round(time.monotonic()-began,4)
        report['truth']=truth
        if truth['status']=='counterexample':
            report['verdict']='review';report['warnings'].append('counterexample')
            if features['layout']=='Triangle' and not truth.get('acute',True):
                report['warnings'].append('possible-acute-only-statement')
        elif truth['status']=='sampled-valid':
            report['verdict']='sampled-valid'
            if features['layout']=='Triangle' and triangle_domain=='acute':
                report['warnings'].append('acute-triangle-required')
        else:
            report['verdict']='review';report['warnings'].append('validation-inconclusive')
        report['qualityScore']=round(100*features['compactness']) if report['verdict']=='sampled-valid' else 0
    report['notice']='Score measures compactness and verification, not beauty, proof difficulty or worldwide novelty.'
    (directory/'quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def audit(job,extending,*,samples=8,milliseconds=2000,workers=2,limit=None,triangle_domain="generic",diagrams=False):
    """Read campaign results without changing its task states or existing diagrams."""
    job=Path(job).resolve()
    with sqlite3.connect((job/'tasks.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        records=[(i,json.loads(e),g) for i,e,g in db.execute("SELECT id,event,geo FROM tasks WHERE state IN ('ready','review') ORDER BY id")]
    if limit is not None:records=records[:limit]
    root=job/'quality-audit';root.mkdir(exist_ok=True)
    def worker(record):
        identifier,event,geo=record
        (root/identifier).mkdir(exist_ok=True)
        report=inspect_event(event,extending,root/identifier,samples=samples,milliseconds=milliseconds,triangle_domain=triangle_domain)
        return dict(id=identifier,report=report,event=event,geo=geo)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows=list(pool.map(worker,records))
    if diagrams:
        import runtime as app
        drawing=subprocess.run([str(app.VIEWER),'--check-geo',str(job/'geo')],capture_output=True,text=True,timeout=120)
        if drawing.returncode:raise ValueError('Geometry diagram checker failed: '+drawing.stderr[:200])
        data=json.loads(drawing.stdout)
        if data.get('status')!='complete':raise ValueError('Unsupported Geometry checker')
        failures={r['file']:r['message'] for r in data['failures']}
        for row in rows:
            if not row['geo'] or not (job/'geo'/row['geo']).is_file():
                failures[row['geo']]='Diagram file is missing'
        (root/'diagrams.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
        for row in rows:
            report=row['report']
            report['diagram']={'status':'not-drawable','message':failures[row['geo']]} if row['geo'] in failures else {'status':'drawable'}
            if row['geo'] in failures:
                report['warnings'].append('diagram-not-drawable')
                if report['verdict']!='standard':report.update(verdict='review',qualityScore=0)
            (root/row['id']/'quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    counts=collections.Counter(r['report']['verdict'] for r in rows)
    warnings=collections.Counter(w for r in rows for w in r['report']['warnings'])
    result={'version':VERSION,'sampleCount':samples,'audited':len(rows),'counts':dict(counts),'warnings':dict(warnings),
            'entries':[dict(id=r['id'],verdict=r['report']['verdict'],theorem=r['event']['theorem'],
                            qualityScore=r['report']['qualityScore'],warnings=r['report']['warnings']) for r in rows]}
    (root/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    text=['# Проверка качества','',f'Проверено: {len(rows)}. Результаты: {dict(counts)}.',
          '', 'Успешные численные пробы не являются доказательством; оценка не измеряет красоту.','']
    for row in rows:
        r=row['report'];text += [f"## {row['id']}: {r['verdict']}", '', '```text',row['event']['configuration'],row['event']['theorem'],'```',
                                '',description(r)+f". Метод: {r['standard'].get('method','—')}.",'']
        if r['truth']['status']=='counterexample':
            text += ['```json',json.dumps(r['truth'],ensure_ascii=False,indent=2),'```','']
    (root/'REPORT.md').write_text('\n'.join(text))
    print(json.dumps({k:v for k,v in result.items() if k!='entries'},ensure_ascii=False),flush=True)
    return result


def description(report):
    truth=report['truth'];features=report['features']
    if truth['status']=='counterexample':
        text='Контрпример: seed '+str(truth.get('sampleSeed','?'))
        if 'possible-acute-only-statement' in report['warnings']:
            text+='; возможно, требуется остроугольность'
    elif truth['status']=='sampled-valid':
        text=str(truth['samples'])+' точных проб'
        if 'acute-triangle-required' in report['warnings']:text+='; только остроугольные треугольники'
    elif report['verdict']=='standard':text='Короткое стандартное доказательство'
    else:text='Проверка истинности не завершена'
    if features['unusedDefinitions']:text+='; не используются: '+', '.join(features['unusedDefinitions'])
    if 'diagram-not-drawable' in report['warnings']:text+='; Geometry не вычисляет картинку'
    return text
