"""A local place directory and user-triggered reference routing; truck validation is explicit."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,threading,time,requests
from psycopg.types.json import Jsonb
from .rates import city_key

class Routing:
 def __init__(self,db):
  self.db=db;self.gate=threading.Lock();self.last=0.
  self.dataset=json.loads((Path(__file__).parent/'data/places.json').read_text(encoding='utf-8'));self.places={p['id']:p for p in self.dataset['places']}
 def search(self,query):
  key=city_key(query)
  if len(key)<2:return []
  found=[p for p in self.places.values() if any(key in city_key(a) for a in p['aliases'])]
  return sorted(found,key=lambda p:(not any(key==city_key(a) for a in p['aliases']),p['name']))[:20]
 def route(self,from_id,to_id):
  if from_id not in self.places or to_id not in self.places:raise ValueError('Выберите города из справочника')
  a,b=self.places[from_id],self.places[to_id];key='osrm:'+from_id+':'+to_id
  with self.db.transaction(admin=True) as c:cached=c.execute('SELECT payload FROM provider_cache WHERE cache_key=%s AND expires_at>now()',(key,)).fetchone()
  if cached:return cached['payload']
  with self.gate:
   delay=1-(time.monotonic()-self.last)
   if delay>0:time.sleep(delay)
   self.last=time.monotonic()
   url=f"https://router.project-osrm.org/route/v1/driving/{a['longitude']},{a['latitude']};{b['longitude']},{b['latitude']}"
   try:r=requests.get(url,params={'overview':'full','geometries':'geojson','steps':'false'},headers={'User-Agent':'NexSearch/1.4 reference routing'},timeout=(5,20));r.raise_for_status();raw=r.json()
   except (requests.RequestException,ValueError):raise ValueError('Открытый сервис маршрутов недоступен. Используйте карты или задайте расстояние вручную.') from None
  if raw.get('code')!='Ok' or not raw.get('routes'):raise ValueError('Маршрут между выбранными городами не найден')
  route=raw['routes'][0];now=datetime.now(timezone.utc)
  result={'origin':a['name'],'destination':b['name'],'distance_km':round(route['distance']/1000,1),'duration_hours':round(route['duration']/3600,1),'geometry':route['geometry'],
          'checked_at':now.isoformat(),'data_version':raw.get('data_version'),'source':'OSRM / OpenStreetMap','source_url':'https://project-osrm.org/',
          'truck_restrictions_checked':False,'message':'Справочный автомобильный маршрут. Высота, масса, осевые ограничения и разрешения на негабаритный проезд здесь не проверены.'}
  with self.db.transaction(admin=True) as c:c.execute('INSERT INTO provider_cache(cache_key,payload,expires_at) VALUES(%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,checked_at=now(),expires_at=excluded.expires_at',(key,Jsonb(result),now+timedelta(hours=12)))
  return result
