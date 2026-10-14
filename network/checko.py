"""Authoritative checks cached by INN. All documented amounts are rubles, not thousands."""
from datetime import datetime,timedelta,timezone
import os,threading,time
import requests
from psycopg.types.json import Jsonb
from collector import valid_inn,unique_phones
from . import host_secrets
class VerificationUnavailable(ValueError):pass
def normalize(company,finances,inn):
 data=company.get('data') or {}
 if data.get('ИНН')!=inn:raise VerificationUnavailable('Чекко вернул другую организацию')
 status=data.get('Статус') or {};active=status.get('Код')=='001' and not (data.get('Ликвид') or {}).get('Дата')
 activity=data.get('ОКВЭД') or {};tax=data.get('Налоги') or {};transport=None
 for entry in tax.get('СведУпл') or []:
  if 'транспорт' in str(entry.get('Наим','')).casefold():transport=entry.get('Сумма');break
 reports={str(y):v for y,v in (finances.get('data') or {}).items() if str(y).isdigit() and isinstance(v,dict)}
 year=max(reports,key=int) if reports else None;latest=reports.get(year,{})
 def money(code):
  value=latest.get(code)
  return value.get('СумОтч') if isinstance(value,dict) else value
 contact=data.get('Контакты') or {};capital=data.get('УстКап') or {};address=data.get('ЮрАдрес') or {}
 return {'inn':inn,'name':data.get('НаимСокр') or data.get('НаимПолн'),'ogrn':data.get('ОГРН'),'kpp':data.get('КПП'),
  'region':(data.get('Регион') or {}).get('Наим',''),'legal_address':address.get('АдресРФ',''),'registration_date':data.get('ДатаРег',''),
  'status':status.get('Наим','Не проверен'),'segment':activity.get('Наим',''),'phones':unique_phones(contact.get('Тел') or []),'emails':contact.get('Емэйл') or [],'website':contact.get('ВебСайт') or '',
  'verification':{'active':active,'status_code':status.get('Код'),'okved':activity.get('Код',''),'additional_okved':[x.get('Код','') for x in data.get('ОКВЭДДоп') or []],
   'source':'Чекко / ФНС','source_url':'https://checko.ru/company/'+str(data.get('ОГРН',inn)),'checked_at':datetime.now(timezone.utc).isoformat()},
  'financial':{'year':int(year) if year else None,'revenue_rub':money('2110'),'profit_rub':money('2400'),'assets_rub':money('1600'),
   'capital_rub':capital.get('Сумма'),'transport_tax_rub':transport,'tax_year':tax.get('СведУплГод'),'reports':reports,
   'report_links':(finances.get('bo.nalog.ru') or {}).get('Отчет') or {},'source':'Чекко / ФНС','currency':'RUB',
   'availability':'complete' if reports else 'not_published','message':'' if reports else 'Финансовая отчётность не опубликована или недоступна по тарифу'},
  'checko_company':data}

class Checko:
 def __init__(self,db):self.db=db;self.gate=threading.Lock();self.last=0.;self.metadata={};self.inn_locks=[threading.Lock() for _ in range(64)];self.slots=threading.BoundedSemaphore(4);self.unavailable_until=0.;self.unavailable_reason=''
 def failure(self):return self.unavailable_reason if time.monotonic()<self.unavailable_until else ''
 def unavailable(self,reason,seconds=30):
  self.unavailable_reason=reason;self.unavailable_until=time.monotonic()+seconds
  return VerificationUnavailable(reason)
 def key(self):return os.environ.get('CHECKO_API_KEY') or host_secrets.load().get('checko_api_key','')
 def request(self,method,inn):
  key=self.key()
  if not key:raise VerificationUnavailable('На хосте не настроен API-ключ Чекко')
  if self.failure():raise VerificationUnavailable(self.failure())
  from .cancellation import check,wait
  while not self.slots.acquire(timeout=.1):check()
  try:
   while not self.gate.acquire(timeout=.1):check()
   try:
    wait(max(0,.35-(time.monotonic()-self.last)));check();self.last=time.monotonic()
   finally:self.gate.release()
   # Pace request starts; never hold the global gate while waiting for the network.
   try:r=requests.get('https://api.checko.ru/v2/'+method,params={'key':key,'inn':inn},timeout=(3,8))
   except requests.RequestException:raise self.unavailable('Чекко временно недоступен') from None
   check()
   try:answer=r.json()
   except ValueError:raise self.unavailable('Чекко: HTTP '+str(r.status_code)+' · нечитаемый ответ') from None
   meta=answer.get('meta') or {};self.metadata={k:meta.get(k) for k in ('today_request_count','balance')}
   if r.status_code!=200 or meta.get('status')!='ok':
    reason=str(meta.get('message') or 'Чекко: HTTP '+str(r.status_code)).replace(key,'[ключ скрыт]')[:300]
    raise self.unavailable(reason,600 if r.status_code in (401,403,429) else 30)
   return answer
  finally:self.slots.release()
 def verify(self,inn,force=False):
  from .cancellation import check
  lock=self.inn_locks[hash(inn)%64]
  while not lock.acquire(timeout=.1):check()
  try:check();return self._verify(inn,force)
  finally:lock.release()
 def _verify(self,inn,force=False):
  if not valid_inn(inn) or len(inn)!=10:raise VerificationUnavailable('Нужен корректный ИНН организации')
  with self.db.transaction(admin=True) as db:
   cached=db.execute('SELECT payload FROM company_checks WHERE inn=%s AND expires_at>now()',(inn,)).fetchone()
  if cached and not force:return cached['payload']
  company=self.request('company',inn);finances={}
  # Inactive companies are rejected before spending a second API call.
  if (company.get('data',{}).get('Статус') or {}).get('Код')=='001':
   try:finances=self.request('finances',inn)
   except VerificationUnavailable as ex:finances={'error':str(ex)}
  result=normalize(company,finances,inn)
  if finances.get('error'):result['financial']['availability']='unavailable';result['financial']['message']=finances['error']
  with self.db.transaction(admin=True) as db:
   db.execute('INSERT INTO company_checks(inn,payload,expires_at) VALUES(%s,%s,%s) ON CONFLICT(inn) DO UPDATE SET payload=excluded.payload,checked_at=now(),expires_at=excluded.expires_at',
    (inn,Jsonb(result),datetime.now(timezone.utc)+timedelta(hours=24)))
  return result
 def enrich(self,item):
  result=self.verify(item.get('inn',''))
  for field in ('phones','emails'):item[field]=list(dict.fromkeys((item.get(field) or [])+(result.get(field) or [])))
  for field in ('name','region','ogrn','kpp','legal_address','registration_date','status','segment','verification','financial','checko_company'):
   if result.get(field) is not None:item[field]=result[field]
  if not item.get('website'):item['website']=result['website']
  item['status_source']=result['verification']['source_url'];return item
