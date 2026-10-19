"""Bounded raster downloads without cookies, credentials, or private-network access."""
import http.client,ssl,time,hashlib,threading
from collections import OrderedDict
from urllib.parse import urlsplit,urljoin,quote
from bs4 import BeautifulSoup
from .mail_html import remote_image_url,raster_uri

class ImageUnavailable(ValueError):pass

def image_urls(html):
    return [n.get('data-mail-remote','') for n in BeautifulSoup(html,'html.parser').select('img[data-mail-image]')][:40]

def download_image(url,connect):
    for _ in range(4):
        url=remote_image_url(url)
        if not url:raise ImageUnavailable('Недопустимый адрес изображения')
        u=urlsplit(url);host=u.hostname.encode('idna').decode('ascii');port=u.port or (443 if u.scheme=='https' else 80)
        sock=None;client=None
        try:
            sock=connect(host,port,timeout=6)
            if u.scheme=='https':sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
            client=http.client.HTTPConnection(host,port,timeout=6);client.sock=sock
            path=quote(u.path or '/',safe='/%:@!$&\'()*+,;=-._~')
            if u.query:path+='?'+quote(u.query,safe='/%?:@!$&\'()*+,;=-._~')
            client.request('GET',path,headers={'User-Agent':'NexSearch-Mail/1.6.3','Accept':'image/png,image/jpeg,image/gif,image/webp','Connection':'close'})
            response=client.getresponse()
            if response.status in (301,302,303,307,308):url=urljoin(url,response.getheader('Location',''));continue
            if response.status!=200:raise ImageUnavailable('Изображение недоступно у отправителя')
            length=response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length)>2*1024**2):raise ImageUnavailable('Изображение больше 2 МБ')
            raw=response.read(2*1024**2+1)
            for kind in ('image/png','image/jpeg','image/gif','image/webp'):
                if raster_uri(raw,kind):return raw,kind
            raise ImageUnavailable('Неподдерживаемый формат изображения')
        except (OSError,ValueError,http.client.HTTPException) as exc:
            if isinstance(exc,ImageUnavailable):raise
            raise ImageUnavailable('Не удалось загрузить изображение') from None
        finally:
            if client:client.close()
            elif sock:sock.close()
    raise ImageUnavailable('Слишком много перенаправлений изображения')

class ImageCache:
    def __init__(self):self.lock=threading.Lock();self.slots=threading.BoundedSemaphore(4);self.items=OrderedDict();self.size=0
    def get(self,owner,url,connect):
        key=(str(owner),hashlib.sha256(url.encode()).hexdigest());now=time.monotonic()
        with self.lock:
            found=self.items.get(key)
            if found and now-found[0]<600:self.items.move_to_end(key);return found[1:]
        if not self.slots.acquire(timeout=2):raise ImageUnavailable('Изображения загружаются. Повторите позже.')
        try:data,kind=download_image(url,connect)
        finally:self.slots.release()
        with self.lock:
            old=self.items.pop(key,None)
            if old:self.size-=len(old[1])
            self.items[key]=(now,data,kind);self.size+=len(data)
            while self.size>32*1024**2:
                _,old=self.items.popitem(last=False);self.size-=len(old[1])
        return data,kind
