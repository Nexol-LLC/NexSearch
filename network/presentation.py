"""Compact list payloads; complete financial records are fetched on opening a card."""
def summary(item):
 result=dict(item);result.pop('checko_company',None)
 from .company_time import company_time
 result['company_time']=company_time(result)
 contacts=result.get('contact_overrides')
 result['contacts_revision']=contacts.get('revision',0) if isinstance(contacts,dict) else 0
 if isinstance(contacts,dict):
  result['source_contacts']={key:list(result.get(key) or []) for key in ('phones','emails')}
  for key in ('phones','emails'):result[key]=list(contacts.get(key) or [])
 if result.get('financial'):result['financial']={k:v for k,v in result['financial'].items() if k not in ('reports','report_links')}
 return result
