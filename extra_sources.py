"""HTML adapters for publicly available company catalogues."""
import re
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup

EXTRA_IDS={'zavody','manufacturers','texzakaz','madeinrussia'}

def card_url(source,url):
    host=urlsplit(url).hostname
    expected=urlsplit(source['url']).hostname
    if host!=expected:return False
    patterns={'zavody':r'/factory/[^/]+/?$', 'manufacturers':r'/company/[^/]+/?$',
              'texzakaz':r'/p/\d+/?$', 'madeinrussia':r'/ru/manufacturers/[^/]+/?$'}
    return bool(re.fullmatch(patterns.get(source['id'],r'(?!)'),urlsplit(url).path))

def listing(source,html,url):
    from collector import canonical
    p=BeautifulSoup(html,'html.parser');kind=source['id']
    selectors={'zavody':'a.factory__title[href]', 'manufacturers':'.sim-title > a[href]',
               'texzakaz':'a.zr-card-name[href]', 'madeinrussia':'.catalogpage__manufacturers > li > a[href]'}
    cards=list(dict.fromkeys(canonical(urljoin(url,a['href'])) for a in p.select(selectors[kind]) if card_url(source,urljoin(url,a['href']))))
    pages=[];seed=urlsplit(source['url']).path.rstrip('/')
    for a in p.select('.pager a[href],.pagination a[href],a[rel="next"],a[href^="/proizvoditeli/"]'):
        page=canonical(urljoin(url,a['href']));parts=urlsplit(page)
        if parts.hostname!=urlsplit(source['url']).hostname or page==canonical(url):continue
        valid=(parts.path.rstrip('/')==seed and ('page=' in parts.query or 'PAGEN_' in parts.query))
        if kind=='texzakaz':valid=bool(re.fullmatch(re.escape(seed)+r'/\d+/?',parts.path))
        if valid and page not in pages:pages.append(page)
    return cards,pages

def labeled_lists(p):
    from collector import text
    result={}
    for li in p.select('.content-list > li'):
        title=li.select_one('.content-list__title');value=li.select_one('.content-list__descr')
        if title and value:result[text(title).rstrip(':')]=text(value)
    return result

def company(source,html,url):
    from collector import clean,text,contacts,website_url,valid_inn,labeled_table,canonical
    p=BeautifulSoup(html,'html.parser');kind=source['id']
    for node in p.select('script,style,noscript'):node.decompose()
    item=dict(name='',inn='',region='',city='',address='',legal_address='',status='Не проверен',feedback='',phones=[],emails=[],website='',
              source=source['name'],source_url=canonical(url),segment='',categories=[],description='',ogrn='',kpp='',registration_date='',director='',
              contact_sources=[],status_source='',notes=[],extra={})
    if kind=='zavody':
        rows=labeled_lists(p)
        item['name']=rows.get('Название юр лица') or text(p.select_one('.company-head h1'))
        item.update(inn=rows.get('ИНН',''),ogrn=rows.get('ОГРН',''),kpp=rows.get('КПП',''),address=rows.get('Адрес',''),segment=rows.get('Тип предприятия',''))
        crumbs=p.select('.company-breadCrumbs a[href^="/factories/"]')
        if crumbs:item['region']=item['city']=text(crumbs[-1])
        node=p.select_one('#company-descr')
        if node:
            copy=BeautifulSoup(str(node),'html.parser')
            for block in copy.select('.content-list'):block.decompose()
            item['description']=text(copy)
        item['phones'],item['emails']=contacts(p.select_one('.company-contact'))
        item['website']=website_url(rows.get('Сайт',''));item['extra']=rows
    elif kind=='manufacturers':
        item['name']=text(p.select_one('.company-page h1'))
        sidebar={}
        for li in p.select('.l-about-comp li'):
            value=li.select_one('span')
            if value:sidebar[clean(text(li).removesuffix(text(value)))]=text(value)
        item['region']=sidebar.get('Регион','');item['city']=sidebar.get('Нас. пункт','')
        contact=p.select_one('#contact-list');rows=labeled_table(contact)
        item['address']=rows.get('Адрес','');item['phones'],item['emails']=contacts(contact)
        site=contact.select_one('a[href^="http"]') if contact else None
        if site and urlsplit(site['href']).hostname!=urlsplit(url).hostname:item['website']=website_url(site['href'])
        item['description']='\n'.join(text(n) for n in p.select('#about > p'))
        categories=labeled_table(p.select_one('#category'))
        item['segment']=categories.get('Отрасль','');item['extra']={**sidebar,**rows,**categories}
        for key,label in [('inn','ИНН'),('ogrn','ОГРН'),('kpp','КПП')]:item[key]=rows.get(label,'')
        if sidebar.get('Статус'):item['notes'].append('Статус в каталоге: '+sidebar['Статус']+'; юридический статус проверяется в ЕГРЮЛ.')
    elif kind=='texzakaz':
        node=p.select_one('#spContent')
        if not node:raise ValueError('Нет открытого профиля производителя')
        item['name']=text(node.select_one('h1'))
        meta=[text(n) for n in node.select('.sp-hero .sp-badge')]
        if meta:item['city']=meta[0]
        if len(meta)>1:item['description']=meta[1]
        match=re.search(r'\bИНН\s+(\d{10,12})\b',text(node))
        if match:item['inn']=match[1]
        item['phones'],item['emails']=contacts(node)
        cats=node.select('a[href^="/zakupki/"]')
        item['categories']=[text(a).removeprefix('Закупки: ').strip() for a in cats]
        item['segment']='; '.join(item['categories'])
        item['extra']['Продукция']=text(node.select_one('.sp-list'))
        if not item['description']:item['description']=item['extra']['Продукция']
    else:
        node=p.select_one('.manufactureritem__right')
        if not node:raise ValueError('Нет открытой карточки производителя')
        item['name']=text(node.select_one('h1'));item['description']=text(node.select_one('.manufactureritem__text'))
        item['phones'],item['emails']=contacts(node.select_one('.manufactureritem__contacts'))
        site=node.select_one('.manufactureritem__buttons-contact a[href^="http"]')
        if site:item['website']=website_url(site['href'])
        for li in node.select('.manufactureritem__details > li'):
            label=text(li.select_one('.manufactureritem__detail-label')).rstrip(':');value=text(li.select_one('.manufactureritem__detail-value'))
            if label:item['extra'][label]=value
            if label.lower() in ('legal address','юридический адрес'):item['legal_address']=item['address']=value
        parts=[x.strip() for x in item['address'].split(',') if x.strip()]
        if parts:item['region']=next((x for x in parts if not x.isdigit()),'')
        # Actual product labels, restricted to this manufacturer's own section.
        labels=p.select('#filter_goods_categories option')
        item['categories']=list(dict.fromkeys(text(n) for n in labels if text(n)))
        item['segment']='; '.join(item['categories'])
    if not item['name'] or len(item['name'])>500:raise ValueError('Не найдено название компании')
    if item['inn'] and not valid_inn(item['inn']):
        item['extra']['Непроверенный ИНН']=item['inn'];item['inn']=''
    if item['phones'] or item['emails']:item['contact_sources'].append(item['source_url'])
    if item['segment'] and not item['categories']:item['categories']=[item['segment']]
    return item
