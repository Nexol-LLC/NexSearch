"""Redistributable XLSX export for the standalone Windows application."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import re
import time
import xlsxwriter

HEADERS=['Наименование','ИНН','Регион регистрации','Часовой пояс','Местное время при выгрузке','Статус','Обратная связь','Номер телефона','Электронная почта','Ссылка на сайт','Источник','Карточка источника','Дата добавления','ОГРН','КПП','Дата регистрации','Руководитель','Адрес','Юридический адрес','Описание компании','Источник контактов','Проверка статуса','Примечания','Позвонили','Описание звонка','Дата звонка (МСК)','Название сегмента']
WIDTHS=[35,16,28,21,23,22,23,25,32,35,27,44,22,20,16,20,35,48,48,85,60,55,85,14,65,23,40]
MSK=timezone(timedelta(hours=3))

def offset(item,zones):
    region=item.get('region','').lower()
    for zone,names in sorted(zones['regions'].items(),key=lambda pair:max(map(len,pair[1])),reverse=True):
        if any(name.lower() in region for name in names):return int(zone)
    if re.fullmatch(r'(?:г\.?\s*)?москва',region):return 3
    if 'саха' in region or 'якутия' in region:
        return zones['cities'].get(item.get('city')) if item.get('city') in ('Якутск','Мирный','Нерюнгри','Верхоянск') else None
    return zones['cities'].get(item.get('city'))

def msk_date(value):
    return datetime.fromisoformat(value).astimezone(MSK).replace(tzinfo=None) if value else None

def write_workbook(items,output,zones_path):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    zones=json.loads(Path(zones_path).read_text(encoding='utf-8'))
    temp=Path(str(output)+'.tmp.xlsx')
    with xlsxwriter.Workbook(temp,{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False}) as book:
        sheet=book.add_worksheet('Клиентская база')
        base={'font_name':'Arial','font_size':11,'valign':'top','text_wrap':True}
        header=book.add_format(dict(base,bold=True,font_color='#FFFFFF',bg_color='#28364A',valign='vcenter'))
        normal=book.add_format(base);striped=book.add_format(dict(base,bg_color='#F2F5F8'))
        dates=[book.add_format(dict(base,num_format='yyyy-mm-dd hh:mm')),book.add_format(dict(base,bg_color='#F2F5F8',num_format='yyyy-mm-dd hh:mm'))]
        sheet.hide_gridlines(2);sheet.freeze_panes(1,1);sheet.set_row(0,48)
        for c,(label,width) in enumerate(zip(HEADERS,WIDTHS)):
            sheet.set_column(c,c,width);sheet.write_string(0,c,label,header)
        now=datetime.now(timezone.utc)
        for r,item in enumerate(items,1):
            zone=offset(item,zones)
            values=[item.get('name',''),item.get('inn',''),item.get('region',''),
                'Не определён' if zone is None else f'UTC+{zone} / МСК'+('' if zone==3 else f'{zone-3:+d}'),
                None if zone is None else (now+timedelta(hours=zone)).replace(tzinfo=None),
                item.get('status','Не проверен'),item.get('feedback',''),'\n'.join(item.get('phones',[])),
                '\n'.join(item.get('emails',[])),item.get('website',''),item.get('source',''),item.get('source_url',''),
                msk_date(item.get('added_at')),item.get('ogrn',''),item.get('kpp',''),item.get('registration_date',''),
                item.get('director',''),item.get('address',''),item.get('legal_address',''),item.get('description',''),
                '\n'.join(item.get('contact_sources',[])),item.get('status_source',''),'\n'.join(item.get('notes',[])),
                'Да' if item.get('called') else 'Нет',item.get('call_note',''),msk_date(item.get('called_at')),item.get('segment','')]
            lines=max(sum(max(1,(len(part)+int(WIDTHS[c])-1)//int(WIDTHS[c])) for part in str(v or '').splitlines()) if v else 1 for c,v in enumerate(values))
            sheet.set_row(r,min(409,max(72,lines*16+12)))
            for c,value in enumerate(values):
                fmt=striped if r%2==0 else normal
                if isinstance(value,datetime):sheet.write_datetime(r,c,value,dates[(r+1)%2])
                elif value is None:sheet.write_blank(r,c,None,fmt)
                else:sheet.write_string(r,c,str(value)[:32767],fmt)
        sheet.autofilter(0,0,len(items),len(HEADERS)-1)
    actual=output
    try:temp.replace(output)
    except PermissionError:
        actual=output.with_name(f'{output.stem}.{time.time_ns()}{output.suffix}');temp.replace(actual)
    manifest=Path(str(output)+'.latest.json');pending=Path(str(manifest)+'.tmp')
    pending.write_text(json.dumps({'file':actual.name,'created':datetime.now(MSK).isoformat(),'calls_revision':sum(x.get('call_revision',0) for x in items)},ensure_ascii=False),encoding='utf-8')
    pending.replace(manifest)
    return actual
