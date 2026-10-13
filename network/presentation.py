"""Compact list payloads; complete financial records are fetched on opening a card."""
def summary(item):
 result=dict(item);result.pop('checko_company',None)
 if result.get('financial'):result['financial']={k:v for k,v in result['financial'].items() if k not in ('reports','report_links')}
 return result
