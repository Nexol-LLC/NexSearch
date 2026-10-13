"""Obtain the portable PostgreSQL runtime from the official Windows distributor."""
from pathlib import Path
import requests
import zipfile

root=Path(__file__).resolve().parent
cache=root/'.runtime';cache.mkdir(exist_ok=True)
archive=cache/'postgresql-17.11-windows-x64.zip'
url='https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip'
if not archive.exists():
    with requests.get(url,stream=True,timeout=60) as response:
        response.raise_for_status()
        pending=archive.with_suffix('.part')
        with pending.open('wb') as handle:
            for chunk in response.iter_content(1024*1024):handle.write(chunk)
        pending.replace(archive)
target=cache/'postgres';target.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    for name in bundle.namelist():
        parts=Path(name).parts
        if len(parts)<3 or parts[0]!='pgsql' or parts[1] not in ('bin','lib','share'):continue
        destination=(target/Path(*parts[1:])).resolve()
        if not destination.is_relative_to(target.resolve()):raise ValueError('Unsafe archive entry')
        if name.endswith('/'):destination.mkdir(parents=True,exist_ok=True)
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(bundle.read(name))
print(target)
