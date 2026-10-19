"""An allowlisted email document; the reader also uses a scriptless CSP sandbox."""
import base64,re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup,Comment

TAGS=set('html head body div span p br hr a b strong i em u s del small sub sup h1 h2 h3 h4 h5 h6 blockquote pre code ul ol li table thead tbody tfoot tr td th caption colgroup col img font center'.split())
DROP=set('script style iframe frame frameset object embed form input button select textarea link meta base svg math template noscript video audio source canvas'.split())
CSS=set('color background-color font-family font-size font-weight font-style text-align text-decoration line-height letter-spacing white-space border border-color border-width border-style border-collapse border-spacing border-radius padding padding-top padding-bottom padding-left padding-right margin margin-top margin-bottom margin-left margin-right width min-width max-width height max-height vertical-align display list-style-type'.split())

def clean_style(value):
    parts=[]
    for item in value.split(';'):
        key,sep,val=item.partition(':');key=key.strip().lower();val=val.strip()
        if sep and key in CSS and len(val)<180 and re.fullmatch(r'[a-zA-Z0-9#%., ()\-+"\']+',val) and not re.search(r'url|expression|javascript|var\s*\(|attr\s*\(',val,re.I):parts.append(key+':'+val)
    return ';'.join(parts)

def raster_uri(raw,kind):
    matches={'image/png':raw.startswith(b'\x89PNG\r\n\x1a\n'),'image/jpeg':raw.startswith(b'\xff\xd8\xff'),'image/gif':raw.startswith((b'GIF87a',b'GIF89a')),'image/webp':raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'}
    if len(raw)>2*1024*1024 or not matches.get(kind):return None
    return 'data:'+kind+';base64,'+base64.b64encode(raw).decode()

def sanitize_html(value,inline=None):
    soup=BeautifulSoup(value[:1_000_000],'html.parser');inline=inline or {}
    for node in list(soup.find_all(string=lambda v:isinstance(v,Comment))):node.extract()
    for node in list(soup.find_all(True)):
        if node.parent is None:continue
        if node.name in DROP:node.decompose();continue
        if node.name not in TAGS:node.unwrap();continue
        attrs={}
        for key,val in list(node.attrs.items()):
            val=str(val)
            if key in ('title','alt'):attrs[key]=val[:1000]
            elif key in ('colspan','rowspan','cellpadding','cellspacing','width','height','size') and re.fullmatch(r'\d{1,4}%?',val):attrs[key]=val
            elif key in ('align','valign') and val.lower() in ('left','right','center','justify','top','middle','bottom'):attrs[key]=val.lower()
            elif key in ('color','bgcolor') and re.fullmatch(r'#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,25}',val):attrs[key]=val
            elif key=='style':attrs[key]=clean_style(val)
            elif key=='href' and node.name=='a':
                try:
                    parsed=urlsplit(val)
                    if parsed.scheme in ('https','http','mailto') and not parsed.username and not parsed.password and not re.search(r'[\x00-\x20]',val):attrs[key]=val[:3000]
                except ValueError:pass
            elif key=='src' and node.name=='img':
                image=inline.get(val[4:].strip('<>')) if val.lower().startswith('cid:') else None
                if image:attrs[key]=image
        node.attrs=attrs
        if node.name=='img' and not attrs.get('src'):node.replace_with(soup.new_string(' ['+attrs.get('alt','Изображение')+'] '))
    body=soup.body or soup
    result=body.decode_contents()
    return result if len(result)<=4_000_000 else '<p>HTML письма слишком большой. Откройте текстовую версию.</p>'
