"""Display the registration-region timezone consistently in lists and cards."""
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
import json
from excel_export import offset

@lru_cache(maxsize=1)
def zones():
    return json.loads((Path(__file__).resolve().parents[1]/'timezones.json').read_text(encoding='utf-8'))

def company_time(item):
    hours=offset(item,zones())
    # Addresses supply a city for otherwise ambiguous multi-zone regions.
    if hours is None:
        address=' '.join(str(item.get(k) or '') for k in ('city','legal_address','address'))
        for city,value in sorted(zones()['cities'].items(),key=lambda kv:len(kv[0]),reverse=True):
            import re
            if re.search(r'(?<!\w)'+re.escape(city)+r'(?!\w)',address,re.I):
                hours=value;break
    if hours is None:return {'utc_offset':None,'label':'Часовой пояс не определён','local_time':None}
    difference=hours-3
    label=f'UTC{hours:+d} · МСК'+(f'{difference:+d}' if difference else '')
    return {'utc_offset':hours,'label':label,'local_time':datetime.now(timezone(timedelta(hours=hours))).isoformat()}
