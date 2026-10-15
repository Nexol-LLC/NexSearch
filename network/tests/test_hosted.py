from test_crm import db,pg,users,http,header
from pathlib import Path
import json

def test_ubuntu_entry_serves_v15_routes(db):
 from fastapi.testclient import TestClient
 from network.server import create_app
 a,b=users(db)
 api=TestClient(create_app(db,start_worker=False))
 response=api.post('/api/vat',headers=header(a),json={'amount':'100','rate':'22','mode':'add'})
 assert response.status_code==200,response.text
 assert response.json()['gross']=='122.00'
 assert api.get('/api/search/schedules',headers=header(a)).status_code==200
 paths={getattr(route,'path','') for route in api.app.routes}
 assert {'/api/carriers/save','/api/export/orders'}<=paths
def test_diagnostics_authorized_and_no_credentials(db,monkeypatch,tmp_path):
 monkeypatch.setenv('NEXOL_HOME',str(tmp_path));a,b=users(db);api=http(db)
 assert api.get('/api/host/status').status_code==401
 result=api.get('/api/host/status',headers=header(a));assert result.status_code==200,result.text
 data=result.json();assert data['status']=='online' and 0<=data['cpu_percent']<=100
 assert data['memory_total_bytes']>data['memory_used_bytes']>0
 assert not any(word in result.text.lower() for word in ['password','api_key','private_key','database_url'])
def test_update_serving_is_authenticated_and_digest_pinned(db,monkeypatch,tmp_path):
 a,b=users(db);packages=tmp_path/'packages';packages.mkdir();digest='a'*64;(packages/(digest+'.zip')).write_bytes(b'signed-by-test-fixture')
 state={'packages':{'EmployeeWindows':{'version':'1.4.2','source_commit':'abc','sha256':digest,'size_bytes':22}}};file=tmp_path/'status.json';file.write_text(json.dumps(state))
 monkeypatch.setenv('NEXSEARCH_UPDATE_STATE',str(file));monkeypatch.setenv('NEXSEARCH_UPDATE_PACKAGES',str(packages));api=http(db)
 assert api.get('/api/update/info').status_code==401
 info=api.get('/api/update/info',headers=header(a)).json();assert info['available'] and info['branch']=='employe'
 assert api.get('/api/update/package?sha256='+digest).status_code==401
 assert api.get('/api/update/package?sha256=wrong',headers=header(a)).status_code==409
 assert api.get('/api/update/package?sha256='+digest,headers=header(a)).content==b'signed-by-test-fixture'
 assert api.get('/api/update/info?platform=HostUbuntu',headers=header(a)).status_code==400
