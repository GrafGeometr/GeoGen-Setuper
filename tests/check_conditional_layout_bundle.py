"""Run against a built bundle: native truth, weakening, .geo routes and diagram arithmetic."""
import json
from pathlib import Path
import subprocess
import sys

bundle=Path(sys.argv[1]).resolve()
output=Path(sys.argv[2]).resolve();output.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(bundle))
import runtime as app
cases=[
 ('triangle','Triangle: A, B, C\nD = Midpoint(A, B)','EqualLineSegments: A, D, C, D','LengthRatio2: A, B, C, D'),
 ('righttriangle','RightTriangle: A, B, C\nD = Midpoint(B, C)','EqualLineSegments: A, B, A, C','PerpendicularLines: [A, D], [B, C]'),
 ('quadrilateral','Quadrilateral: A, B, C, D\nE = Midpoint(A, B)','EqualLineSegments: A, E, C, E','LengthRatio2: A, B, C, E'),
 ('cyclicquadrilateral','CyclicQuadrilateral: A, B, C, D\nE = Midpoint(A, C)','EqualLineSegments: A, B, B, C','PerpendicularLines: [B, E], [A, C]'),
 ('lineandtwopoints','LineAndTwoPoints: l, A, B\nC = Midpoint(A, B)','ParallelLines: l, [A, B]','ParallelLines: l, [A, C]')]
summary={}
for name,config,condition,goal in cases:
    work=output/name;work.mkdir()
    payload=work/'input.json';payload.write_text(json.dumps(dict(configuration=config,condition=condition,theorem=goal,milliseconds=15000)))
    def native(flag):
        r=subprocess.run([str(app.DOTNET),str(bundle/'lib/geogen/GeoGen.MainLauncher.dll'),flag,str(payload)],capture_output=True,text=True,check=True,timeout=25)
        value=json.loads(r.stdout);(work/(flag[2:]+'.json')).write_text(json.dumps(value,indent=2));return value
    truth=native('--verify-conditional')
    assert truth['status']=='accepted',(name,truth)
    extension=native('--extend-conditional')
    assert extension['status'] in ('accepted','rejected'),(name,extension)
    witness=', '.join(map(str,truth['conditionSamples'][0]))
    readable=work/'task.txt'
    readable.write_text('---------\nTheorem 1\n---------\n\n'+config+'\n\nAssume: '+condition+'\nWitness: '+witness+'\n'+goal+' - total ranking 1\n\n'+''.join('  '+a+' weight = 1 ranking = 1 contribution = 1\n' for a in ['Symmetry','Level','NumberOfCyclicQuadrilaterals','NumberOfTheorems']))
    files=app.convert(readable,work/'geo');assert len(files)==1
    result=subprocess.run([str(app.VIEWER),'--check-geo',str(files[0])],capture_output=True,text=True,check=True,timeout=40)
    diagram=json.loads(result.stdout);assert diagram['checked']==1 and not diagram.get('failures'),(name,diagram)
    import conditional
    assert conditional.read_geo_event(files[0])['configuration'].splitlines()[0]==config.splitlines()[0]
    result=subprocess.run([str(app.EXTENDING),'--check-file',str(files[0]),'15000'],capture_output=True,text=True,check=True,timeout=25)
    direct=json.loads(result.stdout);assert direct['status'] in ('accepted','rejected'),(name,direct)
    summary[name]=dict(truth=truth['status'],extending=extension['status'],haskell=direct['status'],diagram='passed')
    print(name,summary[name],flush=True)
(output/'summary.json').write_text(json.dumps(summary,indent=2))
# Actual generator/queue runs, including rigid starts and the user's original story.
policy=output/'conditions.json'
policy.write_text(json.dumps(dict(version=1,seconds=5,samples=4,starts=20,iterations=30,maxConditionsPerConfiguration=8)))
for name in ('triangle-centres','righttriangle','quadrilateral','cyclicquadrilateral','lineandtwopoints','linesegment','lineandpoint','user-quadrilateral'):
    destination=output/('generation-'+name)
    response=subprocess.run([str(bundle/'geometry-suite'),'run',str(bundle/'examples/conditions'/(name+'.txt')),
        '--conditions-config',str(policy),'--output',str(destination),'--no-check','--timeout','30'],capture_output=True,text=True,timeout=70)
    (output/(name+'-generation.log')).write_text(response.stdout+response.stderr)
    assert response.returncode==0,(name,response.stdout,response.stderr)
    log=(destination/'geogen.log').read_text()
    assert ' ERR]' not in log,(name,log)
    trace=[json.loads(line) for line in (destination/'conditions-trace.jsonl').read_text().splitlines()]
    if name in ('linesegment','lineandpoint'):
        assert any(e['kind']=='no-shape-freedom' for e in trace),(name,trace)
    else:
        assert any(e['kind']=='finish' and e['Terminals']>0 for e in trace),(name,trace)
    summary[name+'-generation']='passed'
    print(name,'generator passed',flush=True)
(output/'summary.json').write_text(json.dumps(summary,indent=2))
