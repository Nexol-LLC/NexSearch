"""Automatic industrial-name searches in the public FNS registry, without CAPTCHA bypass."""
import json
import math
import time
from urllib.parse import urlencode

BASE='https://egrul.nalog.ru/'

def search(http,query,region='',page=1):
    result=http.request(BASE,method='POST',data={'query':query,'region':region,'page':str(page),'PreventChromeAutocomplete':''}).json()
    if result.get('captchaRequired') or not result.get('t'):raise ValueError('ЕГРЮЛ запросил капчу или не вернул результат поиска')
    for _ in range(8):
        data=http.request(BASE+'search-result/'+result['t']).json()
        if data.get('status')=='wait':time.sleep(1);continue
        if data.get('status')=='error':raise ValueError('Ошибка поиска ЕГРЮЛ')
        rows=data.get('rows',[])
        if not isinstance(rows,list):raise ValueError('Некорректный ответ ЕГРЮЛ')
        return rows
    raise ValueError('ЕГРЮЛ не завершил поиск за отведённое время')

def company(row,source):
    from collector import valid_inn,classify_fns,datetime,MSK,clean
    if row.get('k')!='ul' or not valid_inn(row.get('i','')) or not row.get('n') or not row.get('o'):raise ValueError('Ответ не содержит подтверждённых реквизитов юридического лица')
    status,note=classify_fns(row)
    return dict(name=clean(row.get('c') or row['n']),inn=row['i'],region=clean(row.get('rn','')),city='',address=clean(row.get('a','')),legal_address=clean(row.get('a','')),
        status=status,feedback='',phones=[],emails=[],website='',source=source['name'],source_url=BASE+'index.html?'+urlencode({'query':row['i']}),
        segment='',categories=[],description='',ogrn=row['o'],kpp=clean(row.get('p','')),registration_date=clean(row.get('r','')),director=clean(row.get('g','')),
        contact_sources=[],status_source=BASE+'index.html — '+datetime.now(MSK).isoformat(timespec='seconds'),notes=[note,'Телефоны и сайт не предоставляются поиском ЕГРЮЛ.'],extra={'ФНС':row})

def discover(store,source,http,max_pages=None):
    from collector import datetime,MSK,LOG
    queries=source.get('queries') or ['завод','фабрика','комбинат','производство']
    # Rotate query starts between runs; the pending queue is always processed first.
    def last_scan(query):
        url=BASE+'index.html?'+urlencode({'query':query})
        row=store.db.execute('SELECT last_scan FROM listing_pages WHERE url=?',(url,)).fetchone()
        return row[0] if row and row[0] else ''
    http.request(BASE+'index.html');scanned=0
    for query in sorted(queries,key=last_scan):
        page=1
        while max_pages is None or scanned<max_pages:
            rows=search(http,query,source.get('region',''),page)
            scanned+=1;count=0
            with store.db:
                for row in rows:
                    try:item=company(row,source)
                    except ValueError:continue
                    store.db.execute('INSERT INTO candidates(url,source,payload) VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload',
                                     (item['source_url'],source['id'],json.dumps(item,ensure_ascii=False)))
                    count+=1
                query_url=BASE+'index.html?'+urlencode({'query':query})
                store.db.execute('INSERT INTO listing_pages(url,source,last_scan) VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET last_scan=excluded.last_scan',
                                 (query_url,source['id'],datetime.now(MSK).isoformat()))
            LOG.info('%s: запрос «%s», страница %d, найдено %d карточек',source['name'],query,page,count)
            yield count
            if not rows:break
            try:total=int(rows[0].get('cnt',len(rows)));current=int(rows[0].get('pg',page))
            except (ValueError,TypeError):break
            # The official service returns 20 rows per page and may cap broad queries.
            if current!=page or page>=math.ceil(total/20):break
            page+=1
