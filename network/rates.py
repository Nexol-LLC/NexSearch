"""Comparable prices keep their original observation date, never the cache timestamp."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import re

def moment(value):
 if isinstance(value,datetime):dt=value
 elif value:dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
 else:return None
 if dt.tzinfo is None:raise ValueError('Дата ставки должна включать часовой пояс')
 return dt.astimezone(timezone.utc)

def freshness(as_of,valid_until=None,now=None,max_age_hours=24,date_precision='timestamp'):
 now=now or datetime.now(timezone.utc);at=moment(as_of);expires=moment(valid_until)
 if not at or date_precision=='unknown':return {'state':'undated','label':'Дата источника отсутствует','age_hours':None}
 age=(now-at).total_seconds()/3600
 if age<-.017:return {'state':'invalid','label':'Дата источника в будущем','age_hours':age}
 if expires and expires<=now:return {'state':'expired','label':'Срок предложения истёк','age_hours':round(age,1)}
 if date_precision=='date':
  days=(now.date()-at.date()).days
  state='fresh' if days==0 else 'previous_day' if days==1 else 'stale'
  return {'state':state,'label':'Данные за сегодня' if days==0 else 'Данные за предыдущий день' if days==1 else 'Устаревшие данные','age_hours':None}
 return {'state':'fresh' if age<=max_age_hours else 'stale','label':'Актуальная ставка' if age<=max_age_hours else 'Устаревшая ставка','age_hours':round(max(0,age),1)}

def net_amount(amount,vat,rate=None):
 if amount is None:return None
 n=Decimal(str(amount))
 if vat=='without':return n
 if vat=='included' and rate is not None:return n/(1+Decimal(str(rate))/100)
 return None

def margin(order):
 customer=net_amount(order.get('client_price'),order.get('client_vat'),order.get('client_vat_rate'))
 supplier=net_amount(order.get('carrier_price'),order.get('carrier_vat'),order.get('carrier_vat_rate'))
 costs=[net_amount(c.get('amount'),c.get('vat'),c.get('vat_rate')) for c in order.get('costs') or []]
 if customer is None or supplier is None or any(c is None for c in costs):
  return {'available':False,'message':'Укажите цены и сопоставимые условия НДС для всех расходов'}
 result=customer-supplier-sum(costs,Decimal(0))
 return {'available':True,'net_customer_rub':round(customer,2),'net_supplier_rub':round(supplier,2),'net_costs_rub':round(sum(costs,Decimal(0)),2),
         'margin_rub':round(result,2),'margin_percent':round(result/customer*100,2) if customer else None,'basis':'Стоимость без включённого НДС; не бухгалтерский расчёт прибыли'}

def city_key(value):return re.sub(r'\s+',' ',str(value).strip().casefold()).replace('ё','е')

def comparable(order,quote,now=None):
 errors=[]
 if city_key(order.get('origin'))!=city_key(quote.get('origin',order.get('origin'))) or city_key(order.get('destination'))!=city_key(quote.get('destination',order.get('destination'))):errors.append('Другое направление')
 if order.get('vehicle_type') and quote.get('vehicle_type',quote.get('vehicle'))!=order['vehicle_type']:errors.append('Другой тип автомобиля')
 stamp=freshness(quote.get('as_of'),quote.get('valid_until'),now,date_precision=quote.get('date_precision','timestamp'))
 if stamp['state'] not in ('fresh',):errors.append(stamp['label'])
 if net_amount(quote.get('amount_rub'),quote.get('vat'),quote.get('vat_rate')) is None:errors.append('Не определены условия НДС')
 return dict(freshness=stamp,comparable=not errors,reasons=errors)
