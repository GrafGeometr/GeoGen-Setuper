from pathlib import Path
import subprocess,json,sys
suite=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(exist_ok=False)
fixtures=[
('ratio2','Triangle: A, B, C\nD = Midpoint(A, B)','LengthRatio2: A, B, A, D',True),
('ratio2-false','Triangle: A, B, C\nD = Midpoint(A, B)','LengthRatio3: A, B, A, D',False),
('ratio3','Triangle: A, B, C\nD = Midpoint(B, C)\nE = Centroid(A, B, C)','LengthRatio3: A, D, E, D',True),
('square2-false','RightTriangle: A, B, C\nD = ReflectionInLineFromPoints(B, A, C)\nE = Midpoint(B, C)','SquaredLengthRatio2: A, B, A, E',False),
('square2','RightTriangle: A, B, C\nD = MidpointOfArc(A, B, C)\nE = PerpendicularProjectionOnLineFromPoints(D, A, B)','SquaredLengthRatio2: A, D, A, E',True),
('angle','Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)','EqualAngles: [A, B], [B, C], [A, D], [D, E]',True),
('angle-false','Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)','EqualAngles: [A, B], [B, C], [D, E], [A, D]',False)]
records=[]
for name,config,goal,expected in fixtures:
 task=out/name;task.mkdir(exist_ok=True);source=task/'check.geo';source.write_text('\n'.join('!'+x for x in (config+'\n'+goal).splitlines())+'\n!\n!\n!\n!\n')
 result=subprocess.run([str(suite/'lib/extending/GeometryExtending'),'--validate-file',str(source),'5000','8','1729','generic'],capture_output=True,text=True,timeout=9)
 assert result.returncode==0,(name,result.stderr)
 response=json.loads(result.stdout);(task/'validation.json').write_text(json.dumps(response,indent=2))
 assert (response['status']=='sampled-valid')==expected,(name,response)
 if not expected:assert response['status']=='counterexample',(name,response)
 # Converter highlights segments/lines without incorrectly marking unequal lengths as equal.
 readable=task/'theorem.txt';readable.write_text('---------\nTheorem 1\n---------\n\n'+config+'\n\n'+goal+' - total ranking 1\n\n'+'  Symmetry weight = 1 ranking = 1 contribution = 1\n'*4)
 geo=task/'geo';geo.mkdir(exist_ok=True)
 result=subprocess.run([str(suite/'lib/java/bin/java'),'-cp',str(suite/'lib/converter/*'),'MainKt',str(readable),str(geo)],capture_output=True,text=True,timeout=30)
 assert result.returncode==0,(name,result.stdout,result.stderr)
 assert len(list(geo.glob('*.geo')))==1,name
 records.append({'fixture':name,'goal':goal,'truth':response,'converted':True})
(out/'summary.json').write_text(json.dumps(records,indent=2))
print('METRIC_FIXTURES_OK',len(records))
