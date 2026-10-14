from pydantic import BaseModel,Field,ConfigDict,field_validator
SEGMENTS={
 '10-100':{'label':'10–100 млн ₽','min':10_000_000,'max':100_000_000},
 '100-500':{'label':'100–500 млн ₽','min':100_000_000,'max':500_000_000},
 '500-1000':{'label':'500 млн – 1 млрд ₽','min':500_000_000,'max':1_000_000_000},
 '1000+':{'label':'1 млрд ₽ и выше','min':1_000_000_000,'max':None}}
DIVISIONS={
 '01':'Сельское хозяйство','02':'Лесное хозяйство','03':'Рыболовство','05':'Добыча угля','06':'Добыча нефти и газа','07':'Добыча руд','08':'Добыча прочих ископаемых','09':'Услуги добычи',
 '10':'Пищевые продукты','11':'Напитки','12':'Табачная продукция','13':'Текстиль','14':'Одежда','15':'Кожа и обувь','16':'Деревообработка','17':'Бумага и картон','18':'Полиграфия',
 '19':'Нефтепродукты','20':'Химическая продукция','21':'Фармацевтика','22':'Резина и пластмассы','23':'Стекло и строительные материалы','24':'Металлургия','25':'Металлические изделия',
 '26':'Электроника и оптика','27':'Электрооборудование','28':'Машины и оборудование','29':'Автомобили','30':'Транспортное оборудование','31':'Мебель','32':'Прочее производство','33':'Ремонт оборудования',
 '35':'Энергетика','36':'Водоснабжение','37':'Канализация','38':'Обработка отходов','39':'Устранение загрязнений','41':'Строительство зданий','42':'Инженерные сооружения','43':'Специализированное строительство',
 '45':'Автотранспорт: торговля и ремонт','46':'Оптовая торговля','47':'Розничная торговля','49':'Наземный транспорт','50':'Водный транспорт','51':'Воздушный транспорт','52':'Склады и транспортные услуги','53':'Почта и курьеры',
 '55':'Гостиницы','56':'Общественное питание','58':'Издательская деятельность','59':'Кино и видеопроизводство','60':'Вещание','61':'Связь','62':'Программное обеспечение','63':'Информационные услуги',
 '64':'Финансовые услуги','65':'Страхование','66':'Вспомогательные финансовые услуги','68':'Недвижимость','69':'Юридические и бухгалтерские услуги','70':'Управление и консалтинг','71':'Архитектура и инженерные работы','72':'Научные исследования','73':'Реклама и маркетинг','74':'Профессиональные услуги','75':'Ветеринария',
 '77':'Аренда и лизинг','78':'Подбор персонала','79':'Туризм','80':'Охрана','81':'Обслуживание зданий','82':'Административные услуги','84':'Государственное управление','85':'Образование','86':'Здравоохранение','87':'Уход с проживанием','88':'Социальные услуги','90':'Искусство','91':'Библиотеки и музеи','92':'Игры и пари','93':'Спорт и отдых','94':'Общественные организации','95':'Ремонт компьютеров и вещей','96':'Персональные услуги','97':'Домашние хозяйства','98':'Производство домашних хозяйств','99':'Экстерриториальные организации'}
class Criteria(BaseModel):
 model_config=ConfigDict(extra='forbid')
 sectors:list[str]=Field(default_factory=list,max_length=90)
 revenue_segments:list[str]=Field(default_factory=list,max_length=4)
 turnover_segments:list[str]=Field(default_factory=list,max_length=4)
 primary_activity_only:bool=True
 region:str=Field(default='',max_length=150)
 keywords:str=Field(default='',max_length=200)
 require_contacts:bool=False
 company_count:int=Field(default=10,ge=1,le=50)
 report_year:int|None=Field(default=None,ge=2011,le=2100)
 @field_validator('sectors')
 @classmethod
 def sectors_valid(cls,values):
  if any(v not in DIVISIONS for v in values):raise ValueError('Неизвестная сфера деятельности')
  return list(dict.fromkeys(values))
 @field_validator('revenue_segments','turnover_segments')
 @classmethod
 def segments_valid(cls,values):
  if any(v not in SEGMENTS for v in values):raise ValueError('Неизвестный сегмент выручки')
  return list(dict.fromkeys(values))

def matches(item,criteria):
 c=criteria if isinstance(criteria,Criteria) else Criteria(**(criteria or {}))
 financial=item.get('financial') or {};checks=item.get('verification') or {}
 if not checks.get('active'):return False
 if c.sectors:
  codes=[str(checks.get('okved',''))]
  if not c.primary_activity_only:codes+=checks.get('additional_okved',[])
  if not any(code[:2] in c.sectors for code in codes):return False
 if c.region and c.region.casefold() not in str(item.get('region','')).casefold():return False
 if c.keywords and c.keywords.casefold() not in (' '.join(str(item.get(k,'')) for k in ('name','description','segment'))).casefold():return False
 if c.require_contacts and not(item.get('phones') or item.get('emails')):return False
 year=str(c.report_year or financial.get('year') or '')
 revenue=(financial.get('reports') or {}).get(year,{}).get('2110')
 if revenue is None and not c.report_year:revenue=financial.get('revenue_rub')
 for selected in (c.revenue_segments,c.turnover_segments):
  if selected and (revenue is None or not any(revenue>=SEGMENTS[s]['min'] and (SEGMENTS[s]['max'] is None or revenue<SEGMENTS[s]['max']) for s in selected)):return False
 return True

def sql_filter(criteria):
 c=Criteria(**(criteria or {}));clauses=[];args=[]
 if c.region:clauses.append("c.payload->>'region' ILIKE %s");args.append('%'+c.region+'%')
 if c.keywords:clauses.append("concat_ws(' ',c.name,c.payload->>'description',c.payload->>'segment') ILIKE %s");args.append('%'+c.keywords+'%')
 if c.require_contacts:clauses.append("(jsonb_array_length(COALESCE(c.payload->'phones','[]'))>0 OR jsonb_array_length(COALESCE(c.payload->'emails','[]'))>0)")
 if c.sectors:
  clause="left(c.payload#>>'{verification,okved}',2)=ANY(%s)";args.append(c.sectors)
  if not c.primary_activity_only:
   clause+=" OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(c.payload#>'{verification,additional_okved}','[]')) v WHERE left(v.value,2)=ANY(%s))";args.append(c.sectors)
  clauses.append('('+clause+')')
 value=f"(c.payload->'financial'->'reports'->'{int(c.report_year)}'->>'2110')::numeric" if c.report_year else "(c.payload#>>'{financial,revenue_rub}')::numeric"
 for selected in (c.revenue_segments,c.turnover_segments):
  ranges=[]
  for segment in selected:
   s=SEGMENTS[segment];expression=value+'>=%s';args.append(s['min'])
   if s['max'] is not None:expression+=' AND '+value+'<%s';args.append(s['max'])
   ranges.append('('+expression+')')
  if ranges:clauses.append('('+' OR '.join(ranges)+')')
 return (' AND '+' AND '.join(clauses)) if clauses else '',args
