from pathlib import Path
import requests
root=Path(__file__).resolve().parents[2]/'PackOnTruck'
vendor=root/'vendor';vendor.mkdir(exist_ok=True)
urls={
 'three.min.js':'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js',
 'xlsx.full.min.js':'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js',
 'three-LICENSE.txt':'https://raw.githubusercontent.com/mrdoob/three.js/r128/LICENSE',
 'xlsx-LICENSE.txt':'https://raw.githubusercontent.com/SheetJS/sheetjs/v0.18.5/LICENSE',
}
for name,url in urls.items():
    p=vendor/name
    if not p.exists():
        response=requests.get(url,timeout=90);response.raise_for_status();p.write_bytes(response.content)
p=root/'index.html';text=p.read_text(encoding='utf-8')
for name,url in urls.items():
    if name.endswith('.js'):text=text.replace(url,'vendor/'+name)
p.write_text(text,encoding='utf-8')
print('Loading dependencies are local.')
