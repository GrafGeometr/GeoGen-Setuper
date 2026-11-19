"""Conditional statements are checked by the native sampler, never as unconditional Haskell goals."""
from pathlib import Path
import json
import math
import os
import re
import subprocess
import time


def check_event(event, directory, milliseconds=15000, *, weakening=True):
    import runtime as app
    app.require_conditional_extending_bundle() if weakening else app.require_conditional_bundle()
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    payload={key:event[key] for key in ('configuration','condition','theorem')}
    if not payload['condition']:raise ValueError('Missing additional condition')
    payload['milliseconds']=milliseconds
    path=directory/'conditional-input.json';path.write_text(json.dumps(payload,ensure_ascii=False))
    env=os.environ.copy();env.update(DOTNET_ROOT=str(app.DOTNET.parent),DOTNET_MULTILEVEL_LOOKUP='0',DOTNET_PROCESSOR_COUNT='1')
    began=time.monotonic()
    try:
        result=subprocess.run([str(app.DOTNET),str(app.ROOT/'lib/geogen/GeoGen.MainLauncher.dll'),'--extend-conditional' if weakening else '--verify-conditional',str(path)],capture_output=True,text=True,
                              timeout=milliseconds/1000+5,start_new_session=True,env=env)
        (directory/'conditional-worker.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise ValueError('Conditional backend exited with '+str(result.returncode))
        report=json.loads(result.stdout)
        if report.get('status') not in ('accepted','rejected','counterexample','inconclusive','timeout','error'):raise ValueError('Invalid conditional check response')
        if report['status']=='accepted' and (report.get('samples',0)<8 or report.get('genericCounterexamples',0)<2 or len(report.get('counterexamples',[]))<2):
            raise ValueError('Incomplete conditional verification')
        if weakening and report['status']=='accepted':
            checked=report.get('weakening',{})
            if checked.get('status')!='accepted' or checked.get('totalVariants')!=checked.get('completedVariants') or len(checked.get('variants',[]))!=checked.get('totalVariants') or any(v.get('status')!='counterexample' for v in checked.get('variants',[])):
                raise ValueError('Incomplete conditional weakening coverage')
    except subprocess.TimeoutExpired:
        report={'status':'timeout','message':'Conditional verification exceeded the process deadline'}
    report['elapsedSeconds']=round(time.monotonic()-began,4)
    (directory/'condition-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def read_geo_event(file):
    """Recover the mathematical statement from converter comments, with exactly one premise."""
    config=[];condition=None;goal=None
    types={'CollinearPoints','ConcyclicPoints','ParallelLines','PerpendicularLines','ConcurrentLines',
           'EqualLineSegments','LineTangentToCircle','TangentCircles','Incidence',
           'LengthRatio2','LengthRatio3','SquaredLengthRatio2','EqualAngles'}
    for line in Path(file).read_text().splitlines():
        line=line.lstrip()
        if not line.startswith('!'):continue
        text=line[1:].strip()
        if text.startswith('Assume:'):
            if condition is not None:raise ValueError('Only one additional condition is supported')
            condition=text.partition(':')[2].strip().removesuffix(' - assumption')
        elif text.startswith('Triangle:') or re.match(r'^\w+\s*=\s*\w+\(',text):config.append(text)
        elif text.partition(':')[0] in types:goal=text.partition(' - total ranking')[0]
    if condition is None:return None
    if not config or not goal:raise ValueError('Incomplete conditional statement in '+str(file))
    return {'configuration':'\n'.join(config).replace('{','').replace('}',''),'condition':condition,'theorem':goal}


def smoke(output):
    """Installed-bundle checks: essentiality, false conclusions, deadlines and a 45° right-angle marker."""
    import runtime as app
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    event={'configuration':'Triangle: A, B, C\nO = Circumcenter(A, B, C)\nH = Orthocenter(A, B, C)',
           'condition':'EqualLineSegments: A, B, A, C','theorem':'CollinearPoints: A, O, H'}
    cases=[('essential',event['theorem'],'accepted',5000),
           ('false','PerpendicularLines: [A, B], [A, C]','counterexample',5000),
           ('unconditional','EqualLineSegments: O, A, O, B','inconclusive',5000),
           ('repeated',event['condition'],'rejected',5000),
           ('timeout',event['theorem'],'timeout',1)]
    statuses={}
    for name,goal,expected,budget in cases:
        report=check_event(dict(event,theorem=goal),output/name,budget,weakening=False)
        if report['status']!=expected:raise RuntimeError('Conditional smoke '+name+': '+str(report))
        statuses[name]=report['status']
    # D=(.5,.5) is equidistant from A/B and AD is perpendicular to BD.
    # The old angle marker intersected these lines with a parallel 45° auxiliary line.
    readable=output/'right-angle.txt'
    readable.write_text('''---------
Theorem 1
---------

Triangle: A, B, C
D = Circumcenter(A, B, C)

Assume: PerpendicularLines: [A, D], [B, D]
Witness: 1.2, 0.4
EqualLineSegments: A, D, B, D - total ranking 1

  Symmetry weight = 1 ranking = 1 contribution = 1
  Level weight = 1 ranking = 1 contribution = 1
  NumberOfCyclicQuadrilaterals weight = 1 ranking = 1 contribution = 1
  NumberOfTheorems weight = 1 ranking = 1 contribution = 1
''')
    files=app.convert(readable,output/'diagram')
    response=subprocess.run([str(app.VIEWER),'--check-geo',str(files[0])],capture_output=True,text=True,timeout=30,check=True)
    diagram=json.loads(response.stdout)
    if diagram.get('checked')!=1 or diagram.get('failures'):raise RuntimeError('Conditional right-angle diagram failed: '+response.stdout)
    redundant=check_event(event,output/'redundant-conditional',5000)
    if redundant['status']!='rejected':raise RuntimeError('Conditional Extending did not detect redundant circumcenter definition: '+str(redundant))
    essential={'configuration':'Triangle: A, B, C\nD = Midpoint(A, B)','condition':'EqualLineSegments: A, D, C, D','theorem':'LengthRatio2: A, B, C, D'}
    necessary=check_event(essential,output/'necessary-conditional',5000)
    if necessary['status']!='accepted':raise RuntimeError('Conditional Extending rejected a necessary definition: '+str(necessary))
    statuses['conditionalExtending']={'redundant':redundant['status'],'necessary':necessary['status']}
    statuses['rightAngleDiagram']='drawable-and-premise-valid'
    (output/'summary.json').write_text(json.dumps(statuses,indent=2))
    return statuses
