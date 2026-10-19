"""An allowlisted email document; the reader also uses a scriptless CSP sandbox."""
import base64,re
from urllib.parse import urlsplit,unquote
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

def remote_image_url(value):
    value=value.strip()
    if value.startswith('//'):value='https:'+value
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or u.port not in (None,443 if u.scheme=='https' else 80):return None
        if len(value)>3000 or re.search(r'[\x00-\x20\x7f\\]',value):return None
        return value.split('#',1)[0]
    except ValueError:return None

def embedded_image(value,inline):
    if value.lower().startswith('cid:'):return inline.get(unquote(value[4:]).strip('<>'))
    if value in inline:return inline[value]
    match=re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,([a-zA-Z0-9+/=\s]+)',value,re.I)
    if match and len(match[2])<=3*1024**2:
        try:return raster_uri(base64.b64decode(match[2],validate=False),match[1].lower())
        except (ValueError,base64.binascii.Error):return None
    return None

def sanitize_html(value,inline=None):
    soup=BeautifulSoup(value[:1_000_000],'html.parser');inline=inline or {};remote_index=0
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
                image=embedded_image(val,inline)
                if image:attrs[key]=image
                elif remote_index<40 and (remote:=remote_image_url(val)):
                    attrs['data-mail-remote']=remote;attrs['data-mail-image']=str(remote_index);remote_index+=1
        node.attrs=attrs
        if node.name=='img' and not attrs.get('src') and 'data-mail-image' not in attrs:node.replace_with(soup.new_string(' ['+attrs.get('alt','Изображение')+'] '))
    body=soup.body or soup
    result=body.decode_contents()
    return result if len(result)<=4_000_000 else '<p>HTML письма слишком большой. Откройте текстовую версию.</p>'
