"""Acceptance on an expendable GitHub Ubuntu VM ONLY. Never run on an office host."""
from pathlib import Path
import hashlib,importlib.util,json,os,secrets,shutil,subprocess,sys,time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def main():
    assert sys.platform=='linux' and os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true','Expendable Ubuntu CI VM required'
    assert not Path('/etc/nexsearch').exists() and not Path('/opt/nexsearch').exists(),'Existing installation is preserved; refusing acceptance setup'
    binary=Path(sys.argv[1]).resolve();old_updater=Path(sys.argv[2]).resolve()
    assert binary.is_file() and old_updater.is_file()
    for path in ('/opt/nexsearch/releases/initial','/opt/nexsearch/control/network','/etc/nexsearch','/var/lib/nexsearch/identity','/var/lib/nexsearch-updater','/var/lib/nexsearch-updates/packages','/var/lib/nexsearch-backups'):
        Path(path).mkdir(parents=True)
    initial=Path('/opt/nexsearch/releases/initial')
    for name in ('network','ubuntu','pack'):shutil.copytree(ROOT/name,initial/name,ignore=shutil.ignore_patterns('__pycache__','.runtime'),dirs_exist_ok=True)
    (initial/'version.json').write_text(json.dumps({'version':'1.6.7','schema':19}));shutil.copy2(ROOT/'timezones.json',initial/'timezones.json')
    (initial/'package-info.json').write_text(json.dumps({'version':'1.6.7','schema':19,'source_commit':'old-ci'}))
    Path('/opt/nexsearch/current').symlink_to(initial)
    run('python3','-m','venv','/opt/nexsearch/venv','--system-site-packages')
    run('/opt/nexsearch/venv/bin/pip','install','-r',str(ROOT/'network/requirements.txt'),stdout=subprocess.DEVNULL)
    run('useradd','--system','--home','/var/lib/nexsearch','--shell','/usr/sbin/nologin','nexsearch')
    sys.path.insert(0,str(ROOT))
    from network.tls import identity
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    trust,cert,key=identity('/var/lib/nexsearch/identity')
    (initial/'network/client-trust.json').write_text(json.dumps(trust))
    signing=Ed25519PrivateKey.generate();public=signing.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    Path('/etc/nexsearch/updates-public.pem').write_bytes(public);(ROOT/'updates-public.pem').write_bytes(public)
    shutil.copy2(old_updater,'/opt/nexsearch/control/old-updater.py');shutil.copy2(ROOT/'network/updates.py','/opt/nexsearch/control/network/updates.py');Path('/opt/nexsearch/control/network/__init__.py').touch()
    password=secrets.token_urlsafe(32);token=secrets.token_urlsafe(32);employee=str(uuid4())
    setup=r'''
import json,sys,hashlib
from pathlib import Path
import psycopg
from psycopg import sql
from argon2 import PasswordHasher
v=json.load(sys.stdin)
with psycopg.connect('dbname=postgres',autocommit=True) as c:
 c.execute('CREATE ROLE nexsearch_owner NOLOGIN')
 c.execute(sql.SQL('CREATE ROLE nexsearch_app LOGIN PASSWORD {}').format(sql.Literal(v['password'])))
 c.execute('CREATE DATABASE nexsearch OWNER nexsearch_owner')
with psycopg.connect('dbname=nexsearch') as c:
 c.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto');c.execute('SET ROLE nexsearch_owner');c.execute("SELECT set_config('nexol.is_admin','true',true)")
 for name in ('schema.sql','schema14.sql','schema15.sql','schema16.sql','schema17.sql','schema18.sql','schema19.sql'):c.execute((Path('/opt/nexsearch/current/network')/name).read_text())
 c.execute("INSERT INTO employees(id,login,first_name,last_name,password_hash,role,requested_role,approved,is_admin) VALUES(%s,'migration','Проверка','Перехода',%s,'director','director',true,true)",(v['employee'],PasswordHasher().hash('fixture-migration-password')))
 c.execute("INSERT INTO sessions(token_hash,employee_id,device_id,expires_at) VALUES(%s,%s,'old-device',now()+interval '30 days')",(hashlib.sha256(v['token'].encode()).hexdigest(),v['employee']))
 c.execute("INSERT INTO carriers(id,name,inn,phone,email,created_by,updated_by) VALUES(gen_random_uuid(),'Сохранённый перевозчик','7700000016','fixture-phone','fixture@example.org',%s,%s)",(v['employee'],v['employee']))
 c.execute('GRANT USAGE ON SCHEMA public TO nexsearch_app');c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nexsearch_app');c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nexsearch_app');c.execute('REVOKE UPDATE,DELETE ON audit_events,call_records FROM nexsearch_app')
 c.commit()
'''
    run('runuser','-u','postgres','--','/opt/nexsearch/venv/bin/python','-c',setup,input=json.dumps({'password':password,'token':token,'employee':employee}),text=True)
    envfile=Path('/etc/nexsearch/host.env');envfile.write_text(f'NEXOL_HOME=/var/lib/nexsearch\nNEXOL_DATABASE_URL="host=127.0.0.1 dbname=nexsearch user=nexsearch_app password={password}"\nNEXOL_HOST_PORT=8743\nNEXSEARCH_UPDATE_STATE=/var/lib/nexsearch-updates/status.json\nNEXSEARCH_UPDATE_PACKAGES=/var/lib/nexsearch-updates/packages\n')
    envfile.chmod(0o640);shutil.chown(envfile,group='nexsearch')
    for folder in (Path('/var/lib/nexsearch'),Path('/var/lib/nexsearch-updates')):
        shutil.chown(folder,user='nexsearch',group='nexsearch')
        for path in folder.rglob('*'):shutil.chown(path,user='nexsearch',group='nexsearch')
    Path('/var/lib/nexsearch-backups').chmod(0o700);shutil.chown('/var/lib/nexsearch-backups',user='postgres',group='postgres')
    unit=Path('/etc/systemd/system/nexsearch.service');unit.write_bytes((ROOT/'ubuntu/nexsearch.service').read_bytes())
    run('systemctl','daemon-reload');run('systemctl','start','nexsearch')
    old=module(Path('/opt/nexsearch/control/old-updater.py'),'old_update_ci');old.configure_retry_units=lambda:None
    assert old.healthy(),'Initial 1.6.7-compatible host failed'
    import requests
    from network.client import PinnedAdapter
    session=requests.Session();session.trust_env=False;session.mount('https://',PinnedAdapter(trust['fingerprint']))
    base='https://127.0.0.1:8743';headers={'Authorization':'Bearer '+token}
    assert session.get(base+'/api/me',headers=headers,timeout=5).json()['employee']['id']==employee
    folder=Path('/opt/native-input');folder.mkdir();shutil.copy2(binary,folder/'nexsearch-host')
    packager=module(ROOT/'next/tools/package-native.py','native_packager_ci')
    archive=Path('/var/lib/nexsearch-updater/host-linux.zip');profile=Path('/etc/nexsearch/client-trust.json');profile.write_text(json.dumps(trust))
    packager.build('HostUbuntu',folder,archive,signing,'native-ci',profile)
    state={'packages':{}}
    old.host_install(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),state)
    assert state['host_installed']['version']=='2.0.0'
    assert 'venv/bin/python' in unit.read_text(),'Test must exercise the original updater first'
    new=module(Path('/opt/nexsearch/control/updater.py'),'new_update_ci')
    assert new.retry_needed(state),'The second stage must run automatically via the retry timer'
    new.activate_native(state)
    assert state['host_runtime']=='rust'
    pid=int(subprocess.check_output(['systemctl','show','nexsearch','--property=MainPID','--value'],text=True))
    assert Path(f'/proc/{pid}/exe').resolve().name=='nexsearch-host'
    me=session.get(base+'/api/me',headers=headers,timeout=5);assert me.status_code==200 and me.json()['employee']['id']==employee
    carriers=session.get(base+'/api/carriers',headers=headers,timeout=5).json()['carriers'];assert any(c['email']=='fixture@example.org' for c in carriers)
    assert session.get(base+'/api/diagnostics',headers=headers,timeout=5).json()['runtime']=='rust'
    assert session.get(base+'/api/export/carriers',headers=headers,timeout=5).status_code==200
    print('PASS: original updater -> native systemd host, pinned TLS, old bearer session, preserved carrier and Excel')
    run('systemctl','stop','nexsearch')

if __name__=='__main__':main()
