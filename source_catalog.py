"""Versioned source defaults. Portable updates add sources without replacing user data."""
SOURCES=[
    dict(id='fabricators',name='Фабрикаторс',url='https://fabricators.ru/produkt/spalnye-meshki',description='Спальные мешки'),
    dict(id='productcenter',name='ProductCenter',url='https://productcenter.ru/producers/r-vladimirskaia-obl-162?sorttype=createdate',description='Владимирская область'),
    dict(id='promreg',name='Промышленные регионы России',url='https://promreg.ru/kompanii/',description='Каталог компаний'),
    dict(id='zavody',name='Заводы.рф',url='https://xn--80aegj1b5e.xn--p1ai/factories',description='Заводы и фабрики России',sitemaps=['https://xn--80aegj1b5e.xn--p1ai/sitemap.xml']),
    dict(id='manufacturers',name='Manufacturers.ru',url='https://manufacturers.ru/companies/proizvoditel',description='Промышленные предприятия',sitemaps=['https://manufacturers.ru/sitemap.xml']),
    dict(id='texzakaz',name='ТехЗаказ',url='https://texzakaz.ru/proizvoditeli',description='Каталог производителей'),
    dict(id='madeinrussia',name='Made in Russia',url='https://madeinrussia.com/ru/manufacturers/',description='Российские производители и экспортёры'),
    dict(id='egrul',name='ЕГРЮЛ (ФНС)',url='https://egrul.nalog.ru/index.html',description='Промышленный поиск и проверка реквизитов',queries=['завод','фабрика','комбинат','производство'])
]
SOURCE_SCHEMA=2

def upgrade_sources(config):
    if config.get('source_schema',1)>=SOURCE_SCHEMA:return False
    existing={s['id'] for s in config.get('sources',[])}
    config.setdefault('sources',[]).extend(dict(s) for s in SOURCES if s['id'] not in existing)
    config['source_schema']=SOURCE_SCHEMA
    return True
