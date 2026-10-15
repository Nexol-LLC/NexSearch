from test_crm import db,pg,users,item,http,header
import io
from openpyxl import load_workbook

def test_working_contacts_survive_scraping_and_are_used_in_clients_and_excel(db):
 a,b=users(db);api=http(db);owner=a['employee']['id']
 source=dict(item(1),phones=['+7 495 100-10-10'],emails=['source@example.org'])
 ident,_,_=db.ingest(source,owner)
 url='/api/leads/'+str(ident)+'/contacts'
 body={'phones':[' +7 900 200-20-20 ','+7 900 200-20-20'],'emails':[' logistics@example.org '],'revision':0}
 assert api.patch(url,json=body).status_code==401
 assert api.patch(url,headers=header(b),json=body).status_code==404
 result=api.patch(url,headers=header(a),json=body)
 assert result.status_code==200,result.text
 assert result.json()=={'phones':['+7 900 200-20-20'],'emails':['logistics@example.org'],'contacts_revision':1}
 assert api.patch(url,headers=header(a),json=body).status_code==409
 db.ingest(dict(source,phones=['+7 495 300-30-30'],emails=['other-source@example.org']))
 lead=api.get('/api/leads',headers=header(a)).json()['companies'][0]
 assert lead['phones']==['+7 900 200-20-20'] and lead['emails']==['logistics@example.org']
 assert '+7 495 300-30-30' in lead['source_contacts']['phones']
 assert lead['contacts_revision']==1
 db.update_lead(owner,ident,'taken',True,'Связались',0)
 assert api.get('/api/clients',headers=header(a)).json()['companies'][0]['phones']==lead['phones']
 for kind in ('leads','clients'):
  response=api.get('/api/export/'+kind,headers=header(a));assert response.status_code==200
  book=load_workbook(io.BytesIO(response.content));row=list(book.active.values)[1]
  assert row[3]=='+7 900 200-20-20' and row[4]=='logistics@example.org';book.close()
 with db.transaction(admin=True) as c:
  event=c.execute("SELECT details FROM audit_events WHERE kind='company.contacts'").fetchone()['details']
  assert event['before']['emails']==['source@example.org']
  assert event['after']['emails']==['logistics@example.org']
 assert not api.get('/api/leads',headers=header(b)).json()['companies']

def test_contact_validation_and_intentional_clearing(db):
 a,b=users(db);api=http(db);ident,_,_=db.ingest(item(1),a['employee']['id'])
 url='/api/leads/'+str(ident)+'/contacts';base={'phones':[],'emails':[],'revision':0}
 for change in ({'emails':['broken']},{'phones':['123']},{'emails':['valid@example.org\nheader']},{'phones':['1'*201]},{'revision':-1}):
  assert api.patch(url,headers=header(a),json={**base,**change}).status_code==422
 assert api.patch(url,headers=header(a),json={**base,'phones':['+7 900 111-11-11']}).status_code==200
 result=api.patch(url,headers=header(a),json={**base,'revision':1})
 assert result.status_code==200 and result.json()['phones']==[]
 db.ingest(dict(item(1),phones=['+7 495 100-00-00']))
 assert api.get('/api/leads',headers=header(a)).json()['companies'][0]['phones']==[]

def test_scraper_and_legacy_import_cannot_supply_working_contact_overrides(db):
 a,b=users(db)
 source=dict(item(2),phones=['+7 495 111-11-11'],contact_overrides={'phones':['+7 900 999-99-99'],'emails':[],'revision':1})
 db.ingest(source,a['employee']['id'])
 lead=db.leads(a['employee']['id'])[0]
 assert lead['phones']==source['phones'] and lead['contacts_revision']==0
