from test_crm import db,pg,users,http,header,item
from network.criteria import Criteria,matches
from network.search15 import SearchWorker
from psycopg.types.json import Jsonb
import pytest
from datetime import datetime,timedelta,timezone


def test_timezone_criteria_validated_saved_and_combined(db):
    a,b=users(db);api=http(db)
    assert Criteria(timezones=[7,3,7]).timezones==[3,7]
    for invalid in ([0],[13],['bad']):
        with pytest.raises(ValueError):Criteria(timezones=invalid)
    path='/api/search/preferences'
    response=api.put(path,headers=header(a),json={'timezones':[4,7],'region':'Самарская','company_count':8})
    assert response.status_code==200,response.text
    stored=api.get(path,headers=header(a)).json()
    assert stored['criteria']['timezones']==[4,7]
    scheduled=api.post('/api/search/schedules',headers=header(a),json={'run_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),'target':5,'criteria':stored['criteria']})
    assert scheduled.status_code==200,scheduled.text
    assert api.get('/api/search/schedules',headers=header(a)).json()['schedules'][0]['criteria']['timezones']==[4,7]
    catalog=api.get('/api/catalog',headers=header(a)).json();assert [z['offset'] for z in catalog['timezones']]==list(range(2,13))
    samara={**item(1),'region':'Самарская область'}
    assert matches(samara,{'timezones':[4,7],'region':'Самарская','sectors':['43']})
    assert not matches(samara,{'timezones':[3]})
    assert not matches({**item(2),'region':'Не указан'}, {'timezones':[3]})
    assert not matches({**item(3),'region':'Республика Саха (Якутия)'},{'timezones':[9]})
    assert matches({**item(3),'region':'Республика Саха (Якутия)','legal_address':'г. Якутск, ул. Ленина'}, {'timezones':[9]})


def test_cached_timezone_filter_is_applied_before_limit_and_backfills_old_rows(db):
    a,b=users(db)
    # More earlier records than the target; a late matching company must be found.
    for n in range(100,120):db.ingest(item(n))
    wanted=db.ingest({**item(140),'region':'Самарская область','timezone_utc_offset':3})[0]
    with db.transaction(admin=True) as c:
        assert c.execute('SELECT payload FROM companies WHERE id=%s',(wanted,)).fetchone()['payload']['timezone_utc_offset']==4
        c.execute("UPDATE companies SET payload=payload-'timezone_utc_offset'")
    assert db.claim_available(a['employee']['id'],1,criteria={'timezones':[4]})==[str(wanted)]
    assert not db.claim_available(b['employee']['id'],1,criteria={'timezones':[4]})
    assert len(db.claim_available(b['employee']['id'],2,criteria={'timezones':[3]}))==2
    with db.transaction(admin=True) as c:
        assert c.execute("SELECT count(*) AS n FROM companies WHERE NOT payload ? 'timezone_utc_offset'").fetchone()['n']==0


def test_fresh_source_candidates_follow_timezone_and_remain_unassigned_on_mismatch(db):
    a,b=users(db);api=http(db)
    job=api.post('/api/search',headers=header(a),json={'target':1,'criteria':{'timezones':[7]}}).json()['id']
    with db.transaction(admin=True) as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        job=c.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
    class Checker:
        def enrich(self,company):return company
    worker=SearchWorker(db,Checker())
    for n,region in [(51,'Москва'),(52,'Новосибирская область')]:
        worker.process_card({'url':'https://example.org/company/'+str(n),'payload':{**item(n),'region':region}}, {'id':'egrul','name':'ФНС'},job,a['employee']['id'],{'timezones':[7]})
    companies=db.leads(a['employee']['id'])
    assert len(companies)==1 and companies[0]['region']=='Новосибирская область'


def test_pdf_activity_is_supported_without_broadening_activity_validation(db):
    a,b=users(db);api=http(db)
    assert api.post('/api/activity',headers=header(a),json={'kind':'external.pdf'}).status_code==200
    assert api.post('/api/activity',headers=header(a),json={'kind':'external.invalid'}).status_code==422
