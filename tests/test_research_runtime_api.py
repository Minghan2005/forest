"""Real local API/database checks for editable planning and 200-node batches."""
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_runtime_api_isolated(tmp_path):
 env={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'api.db'),'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_MODEL':'','PYTHONPATH':str(ROOT)}
 p=subprocess.run([sys.executable,__file__,'run'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
 assert p.returncode==0,p.stdout+p.stderr
if __name__=='__main__':
 from fastapi.testclient import TestClient
 from sqlalchemy import event

 from research.planning.loop import apply_plan
 from services.api.db import Edge, Project, Session, TaskRun, engine, uid
 from services.api.main import app
 with TestClient(app) as c:
  p=c.post('/api/projects',json={'name':'Real graph scalability check','goal':'Execute user-defined experiments','mode':'manual'}).json();pid=p['id']
  assert p['budget']=={'allow_paid':False}
  g=c.get(f'/api/projects/{pid}/graph').json();bid=g['branches'][0]['id']
  commands=[{'operation':'add_node','params':{'id':f'n{i}','title':f'Task {i}','type':'implementation','instructions':'Read and process real input files','config':{'kind':'agent'},'branch_id':bid,**({'parent_id':f'n{i-1}'} if i else {})}} for i in range(200)]
  body={'request_id':'actual-batch','expected_revision':0,'commands':commands}
  r=c.post(f'/api/projects/{pid}/graph/batch',json=body);assert r.status_code==200,r.text
  assert r.json()['node_count']==200
  assert c.post(f'/api/projects/{pid}/graph/batch',json=body).json()==r.json()
  page=c.get(f'/api/projects/{pid}/graph/page?offset=100&limit=40').json();assert len(page['nodes'])==40
  page_ids={node['id'] for node in page['nodes']}
  first,last=page['nodes'][0]['id'],page['nodes'][-1]['id']
  all_ids={node['id'] for node in c.get(f'/api/projects/{pid}/graph').json()['nodes']}
  outside,outside2=list(all_ids-page_ids)[:2]
  other=c.post('/api/projects',json={'name':'Separate graph project','goal':'Isolate project edges','mode':'manual'}).json()
  with Session.begin() as s:
   s.add(Edge(project_id=pid,source=outside,target=first,relation='incoming-test'))
   s.add(Edge(project_id=pid,source=last,target=outside,relation='outgoing-test'))
   s.add(Edge(project_id=pid,source=outside,target=outside2,relation='unrelated-test'))
   s.add(Edge(project_id=other['id'],source=first,target=outside,relation='foreign-project-test'))
  edge_queries=[]
  def capture_edge_query(conn,cursor,statement,parameters,context,executemany):
   normalized=' '.join(statement.lower().replace(chr(34),'').split())
   if 'select ' in normalized and ' from edges ' in (' '+normalized+' '):
    edge_queries.append(normalized)
  event.listen(engine,'before_cursor_execute',capture_edge_query)
  try:
   page=c.get(f'/api/projects/{pid}/graph/page?offset=100&limit=40').json()
   empty=c.get(f'/api/projects/{pid}/graph/page?offset=1000&limit=40').json()
  finally:
   event.remove(engine,'before_cursor_execute',capture_edge_query)
  assert len(page['nodes'])==40
  assert empty['nodes']==[] and empty['edges']==[]
  assert len(edge_queries)==1,edge_queries
  assert 'where' in edge_queries[0],edge_queries[0]
  edge_where=edge_queries[0].split('where',1)[1]
  assert 'edges.source in' in edge_where and 'edges.target in' in edge_where
  g=c.get(f'/api/projects/{pid}/graph').json()
  assert len(g['edges'])==202
  expected=[edge for edge in g['edges'] if edge['source'] in page_ids or edge['target'] in page_ids]
  assert page['edges']==expected
  assert any(edge['relation']=='incoming-test' for edge in page['edges'])
  assert any(edge['relation']=='outgoing-test' for edge in page['edges'])
  assert not any(edge['relation'] in {'unrelated-test','foreign-project-test'} for edge in page['edges'])
  assert any((edge['source'] in page_ids)!=(edge['target'] in page_ids) for edge in page['edges'])
  positions={f'n{i}':{'x':i*5,'y':i*3} for i in range(200)}
  layout=c.post(f'/api/projects/{pid}/graph/batch',json={'request_id':'atomic-layout','expected_revision':g['revision'],'commands':[{'operation':'edit_node','targets':list(positions),'params':{'positions':positions}}]})
  assert layout.status_code==200,layout.text
  moved=c.get(f'/api/projects/{pid}/graph').json();assert moved['revision']==g['revision']+1
  assert {n['id']:n['position'] for n in moved['nodes'] if n['id'] in positions}==positions
  undo=c.post(f'/api/projects/{pid}/graph/commands',json={'request_id':uid(),'expected_revision':moved['revision'],'operation':'undo'});assert undo.status_code==200,undo.text;assert len(undo.json()['graph']['nodes'])==200
  undo=c.post(f'/api/projects/{pid}/graph/commands',json={'request_id':uid(),'expected_revision':undo.json()['graph']['revision'],'operation':'undo'});assert undo.status_code==200,undo.text;assert len(undo.json()['graph']['nodes'])==0
  c.post(f'/api/projects/{pid}/research/start',json={'autonomous':True}).raise_for_status()
  with Session() as s:current=s.get(Project,pid).revision
  try: apply_plan(pid,'validation',{'action':'completed','rationale':'No observed scientific outputs','commands':[]},current)
  except ValueError as e: assert 'evidence' in str(e)
  else: raise AssertionError('Unmeasured completion was accepted')
  proposed={'action':'continue','rationale':'Implement a real baseline before comparing candidates','commands':[{'operation':'add_node','params':{'id':'real-work','type':'implementation','title':'Implement baseline','instructions':'Read project inputs and implement a baseline','config':{'kind':'agent'},'branch_id':bid}}]}
  result=apply_plan(pid,'validation',proposed,current);assert result['applied_commands']==1
  state=c.get(f'/api/projects/{pid}/research').json();assert len(state['decisions'])==1
  objective=c.patch(f'/api/projects/{pid}/objective',json={'metric':'accuracy','direction':'max'});assert objective.status_code==200,objective.text
  assert c.get(f'/api/projects/{pid}/research').json()['objective']['direction']=='max'
  invalid_objective=c.patch(f'/api/projects/{pid}/objective',json={'metric':'accuracy','direction':'sideways'});assert invalid_objective.status_code==422
  with Session.begin() as s:
   queued=TaskRun(project_id=pid,request_id=uid(),kind='command',status='queued',config={'command':['true']},output_path='runs/real-control-check');s.add(queued);s.flush();rid=queued.id
  paused=c.post(f'/api/projects/{pid}/research/pause',json={});assert paused.status_code==200,paused.text;assert paused.json()['process_control_errors']==[]
  with Session() as s:assert s.get(TaskRun,rid).status=='paused'
  resumed=c.post(f'/api/projects/{pid}/research/start',json={'autonomous':False});assert resumed.status_code==200,resumed.text;assert resumed.json()['process_control_errors']==[]
  with Session() as s:assert s.get(TaskRun,rid).status=='queued'
  stopped=c.post(f'/api/projects/{pid}/research/stop',json={});assert stopped.status_code==200,stopped.text;assert stopped.json()['process_control_errors']==[]
  with Session() as s:assert s.get(TaskRun,rid).status=='cancelled'
  from services.worker.scheduler import enqueue
  with Session.begin() as s:
   limited=enqueue(s,pid,'command',{'command':['true'],'budget':{'seconds':7200}});assert limited.config['timeout']==7200
   unlimited=enqueue(s,pid,'command',{'command':['true'],'budget':{'seconds':7200},'timeout':None});assert unlimited.config['timeout'] is None
  writing=c.post(f'/api/projects/{pid}/writing/review',json={'source':'Unfortunately, the method merely reduces error.'});assert writing.status_code==200,writing.text
  invalid=c.post(f'/api/projects/{pid}/protocol/validate',json={});assert invalid.status_code==422,invalid.text
  print('Real graph batch, one-step undo, planning evidence checks and writing API passed')
