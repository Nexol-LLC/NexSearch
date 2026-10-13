"""Offline signed program updates, validated before staging and applied after exit."""
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
from uuid import uuid4
import json,zipfile,hashlib,os,shutil,time
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.exceptions import InvalidSignature

class UpdateError(ValueError):pass
def safe_target(base,name):
 base=Path(base).resolve();rel=PurePosixPath(name)
 if not name or '\\' in name or ':' in name or rel.is_absolute() or '..' in rel.parts:raise UpdateError('Недопустимый путь в обновлении')
 target=(base/Path(*rel.parts)).resolve()
 if not target.is_relative_to(base):raise UpdateError('Файл обновления выходит за папку программы')
 return target

def prepare(archive,program,home,role):
 program=Path(program).resolve();home=Path(home).resolve();public=program/'updates-public.pem'
 if role not in ('HostWindows','EmployeeWindows','EmployeeMac','HostUbuntu'):raise UpdateError('Неизвестная платформа обновления')
 if not public.is_file():raise UpdateError('Нет доверенного ключа обновлений')
 with zipfile.ZipFile(archive) as z:
  try:raw=z.read('release.json');signature=z.read('release.sig');load_pem_public_key(public.read_bytes()).verify(signature,raw)
  except (KeyError,InvalidSignature,ValueError):raise UpdateError('Подпись обновления не подтверждена') from None
  manifest=json.loads(raw)
  if manifest.get('role')!=role or manifest.get('schema',0)<14:raise UpdateError('Обновление не подходит роли или версии базы')
  files=manifest.get('files');prefix=role+'/'
  if not isinstance(files,dict) or not files or len(files)>20000:raise UpdateError('Некорректный список файлов')
  allowed=set(files)|{'release.json','release.sig'}
  if any(not e.is_dir() and e.filename not in allowed for e in z.infolist()):raise UpdateError('Архив содержит неподписанные файлы')
  total=sum(e.file_size for e in z.infolist())
  if total>4*1024**3:raise UpdateError('Обновление слишком большое')
  folder=home/'updates'/('stage-'+str(uuid4()));folder.mkdir(parents=True,exist_ok=True)
  for name,digest in files.items():
   if not name.startswith(prefix):raise UpdateError('Файл относится к другой программе')
   rel=name[len(prefix):];target=safe_target(folder,rel)
   if rel=='updates-public.pem' and hashlib.sha256(public.read_bytes()).hexdigest()!=digest:raise UpdateError('Замена доверенного ключа требует отдельной процедуры')
   entry=z.getinfo(name)
   if (entry.external_attr>>16)&0o170000==0o120000:raise UpdateError('Ссылки в обновлениях Windows не поддерживаются')
   data=z.read(entry)
   if hashlib.sha256(data).hexdigest()!=digest:raise UpdateError('Контрольная сумма не совпала')
   target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  state={'program':str(program),'stage':str(folder),'role':role,'version':manifest['version'],'files':{n[len(prefix):]:v for n,v in files.items()},'verified_at':datetime.now(timezone.utc).isoformat()}
  # Keep the signature alongside the stage for verification at application time.
  (folder/'release.json').write_bytes(raw);(folder/'release.sig').write_bytes(signature)
  state_file=folder/'update-state.json';state_file.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
  return {'version':state['version'],'files':len(files),'state_file':str(state_file),'verified':True}

def apply(state_file,timeout=120):
 state_file=Path(state_file).resolve();state=json.loads(state_file.read_text(encoding='utf-8'));program=Path(state['program']).resolve();stage=Path(state['stage']).resolve()
 if state_file.parent!=stage:raise UpdateError('Неверная папка подготовки')
 public=program/'updates-public.pem';manifest_raw=(stage/'release.json').read_bytes()
 try:load_pem_public_key(public.read_bytes()).verify((stage/'release.sig').read_bytes(),manifest_raw)
 except (InvalidSignature,ValueError):raise UpdateError('Подпись подготовки не подтверждена') from None
 signed=json.loads(manifest_raw);expected={n[len(state['role'])+1:]:v for n,v in signed['files'].items()}
 if signed['role']!=state['role'] or expected!=state['files']:raise UpdateError('Список подготовки изменён')
 for name,digest in expected.items():
  p=safe_target(stage,name)
  if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise UpdateError('Подготовленные файлы изменены')
  safe_target(program,name)
 deadline=time.monotonic()+timeout
 while True:
  try:
   for name in expected:
    p=safe_target(program,name)
    if p.suffix.lower()=='.exe' and p.exists():
     with p.open('r+b'):pass
   break
  except PermissionError:
   if time.monotonic()>deadline:raise UpdateError('Закройте программу перед установкой обновления')
   time.sleep(1)
 rollback=stage.parent/('rollback-'+datetime.now().strftime('%Y%m%d-%H%M%S'));rollback.mkdir(exist_ok=True)
 replaced=[]
 try:
  for name in expected:
   destination=safe_target(program,name);source=safe_target(stage,name);backup=safe_target(rollback,name)
   if destination.exists():backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,backup)
   destination.parent.mkdir(parents=True,exist_ok=True);pending=destination.with_name(destination.name+'.nex-update');shutil.copy2(source,pending);pending.replace(destination);replaced.append(name)
 except Exception:
  for name in reversed(replaced):
   old=safe_target(rollback,name)
   if old.exists():shutil.copy2(old,safe_target(program,name))
  raise UpdateError('Установка не завершена; предыдущие файлы восстановлены') from None
 (rollback/'rollback-state.json').write_text(json.dumps({'program':str(program),'files':replaced,'created_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False),encoding='utf-8')
 return {'installed':state['version'],'rollback':str(rollback)}

def rollback(folder):
 folder=Path(folder).resolve();state=json.loads((folder/'rollback-state.json').read_text(encoding='utf-8'));program=Path(state['program']).resolve()
 for name in state['files']:
  source=safe_target(folder,name);target=safe_target(program,name)
  if source.is_file():shutil.copy2(source,target)
 return {'restored':True}
