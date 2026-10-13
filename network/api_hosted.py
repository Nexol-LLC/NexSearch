"""Authenticated diagnostics and signed employee updates cached from employe."""
from pathlib import Path
from datetime import datetime,timezone
from contextlib import asynccontextmanager
import json,os,socket,psutil
from fastapi import Depends,Request,HTTPException
from fastapi.responses import FileResponse
from .api14 import create_app as base_app
from .version import VERSION

def create_app(*args,**kwargs):
 app=base_app(*args,**kwargs);auth=app.state.auth
 def employee(request:Request):
  token=request.headers.get('authorization','')
  try:return auth.authenticate(token[7:] if token.startswith('Bearer ') else '',request.client.host)
  except PermissionError as error:raise HTTPException(401,str(error))
 def update_state():
  try:return json.loads(Path(os.environ.get('NEXSEARCH_UPDATE_STATE','/var/lib/nexsearch-updates/status.json')).read_text(encoding='utf-8'))
  except (OSError,ValueError):return {'status':'not_configured','branches':{}}
 @app.get('/api/host/status')
 def host_status(request:Request,user=Depends(employee)):
  memory=psutil.virtual_memory();disk=psutil.disk_usage(os.environ.get('NEXOL_HOME','/var/lib/nexsearch'))
  with app.state.db.transaction(user['id']) as conn:queue=conn.execute("SELECT count(*) FILTER(WHERE status='running') AS running,count(*) FILTER(WHERE status='queued') AS queued FROM jobs").fetchone()
  return {'version':VERSION,'status':'online','ip':request.url.hostname,'hostname':socket.gethostname(),'time':datetime.now(timezone.utc),'cpu_percent':psutil.cpu_percent(),'memory_used_bytes':memory.total-memory.available,'memory_total_bytes':memory.total,'memory_percent':memory.percent,'disk_free_bytes':disk.free,'process_memory_bytes':psutil.Process().memory_info().rss,'queue':queue,'updates':update_state(),'schedule':'10:00 Europe/Moscow'}
 @app.get('/api/update/info')
 def update_info(platform:str='EmployeeWindows',user=Depends(employee)):
  if platform not in ('EmployeeWindows','EmployeeMac'):raise HTTPException(400,'Неизвестная платформа')
  state=update_state();package=state.get('packages',{}).get(platform)
  if not package:return {'available':False,'branch':'employe','checked_at':state.get('checked_at'),'status':state.get('status','not_configured')}
  return {'available':True,'branch':'employe','checked_at':state.get('checked_at'),**{k:package[k] for k in ('version','source_commit','sha256','size_bytes')}}
 @app.get('/api/update/package')
 def update_package(platform:str='EmployeeWindows',sha256:str='',user=Depends(employee)):
  if platform not in ('EmployeeWindows','EmployeeMac'):raise HTTPException(400,'Неизвестная платформа')
  state=update_state();package=state.get('packages',{}).get(platform)
  if not package or package.get('sha256')!=sha256:raise HTTPException(409,'Комплект изменился; проверьте обновления снова')
  root=Path(os.environ.get('NEXSEARCH_UPDATE_PACKAGES','/var/lib/nexsearch-updates/packages')).resolve();file=(root/(package['sha256']+'.zip')).resolve()
  if not file.is_relative_to(root) or not file.is_file():raise HTTPException(503,'Комплект ещё загружается')
  app.state.db.audit(user['id'],'update.employee_download',platform,{'branch':'employe','version':package['version'],'source_commit':package['source_commit']})
  return FileResponse(file,media_type='application/zip',filename=platform+'-update.zip')
 return app
