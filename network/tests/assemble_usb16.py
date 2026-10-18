"""Prepare review kits from current code and cached offline runtimes; no user data."""
from pathlib import Path
import hashlib,io,json,plistlib,shutil,subprocess,sys,tarfile,zipfile

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from package_update import source_files
OLD=ROOT.parent/'server-deploy/NexSearch1.4'
OUT=ROOT/'preview/usb16/NexSearch1.6.0'
COMMIT=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()

def metadata(role):return json.dumps({'version':'1.6.0','schema':16,'role':role,'source_commit':COMMIT,'local_review':True},indent=2).encode()

def files(platform):
    for path,name in source_files():
        if name.startswith('network/bin/') and not name.startswith('network/bin/'+platform+'-'):continue
        yield path,name

def archive(role,destination,platform):
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path,name in files(platform):
            mode=0o755 if path.name=='nexsearch-vpn' or path.suffix in ('.sh','.zsh') else 0o644
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(0o100000|mode)<<16;info.compress_type=zipfile.ZIP_DEFLATED
            data=path.read_bytes()
            if path.suffix in ('.sh','.zsh','.service','.timer'):data=data.replace(b'\r\n',b'\n')
            z.writestr(info,data)
        z.writestr('client-trust.json',(ROOT/'network/client-trust.json').read_bytes());z.writestr('package-info.json',metadata(role))

def windows(kind,label):
    target=OUT/label/'Windows';target.mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'dist'/kind,target,dirs_exist_ok=True)
    current=ROOT.parent/'NexSearch-1.6.0'/('Хост Windows' if kind=='HostWindows' else 'Сотрудник Windows')
    shutil.copytree(current/'desktop/runtime',target/'desktop/runtime',dirs_exist_ok=True)
    shutil.copy2(ROOT/'dist/Maintenance/NexSearch Maintenance.exe',target/'NexSearch Maintenance.exe')
    if kind=='HostWindows':shutil.copytree(current/'postgres',target/'postgres',dirs_exist_ok=True)
    (target/'package-info.json').write_bytes(metadata(kind));print('Prepared '+kind,flush=True)

def macos(role,label):
    kind='Host' if role=='host' else 'Employee';old=next((OLD/kind/'macOS').glob('*.app'))
    folder=OUT/label/'macOS';folder.mkdir(parents=True,exist_ok=True)
    app=folder/f'NexSearch 1.6.0 {label}.app';resources=app/'Contents/Resources';resources.mkdir(parents=True,exist_ok=True)
    for name in ('Runtimes','Wheels'):shutil.copytree(old/'Contents/Resources'/name,resources/name,dirs_exist_ok=True)
    shutil.copytree(old/'Contents/Resources/../MacOS',app/'Contents/MacOS',dirs_exist_ok=True)
    plist=plistlib.loads((old/'Contents/Info.plist').read_bytes())
    plist.update(CFBundleName='NexSearch',CFBundleDisplayName='NexSearch 1.6.0 '+label,CFBundleShortVersionString='1.6.0',CFBundleVersion='1.6.0',LSMinimumSystemVersion='12.0')
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps(plist))
    for p in (old/'Contents/Resources').glob('*.icns'):shutil.copy2(p,resources/p.name)
    (app/'Contents/MacOS/NexSearch').write_text((ROOT/'network/mac_start.zsh').read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    (resources/'role.txt').write_text(role);(resources/'build-id.txt').write_text('nexsearch-1.6.0-'+COMMIT[:12]+'-transport')
    shutil.copy2(ROOT/'network/mac_installer.py',resources/'installer.py')
    archive('HostMac' if role=='host' else 'EmployeeMac',resources/'source.zip','darwin')
    checks=[]
    for p in sorted(resources.rglob('*')):
        if p.is_file() and p.name!='checksums.txt':checks.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(resources).as_posix())
    (resources/'checksums.txt').write_text('\n'.join(checks)+'\n',encoding='utf-8',newline='\n')
    (folder/'Запустить NexSearch.command').write_text('#!/bin/zsh\nset -eu\ncd -- "${0:A:h}"\nexec /bin/zsh "'+app.name+'/Contents/MacOS/NexSearch"\n',encoding='utf-8',newline='\n')
    print('Prepared macOS '+role,flush=True)

def linux():
    folder=OUT/'Хост/Linux Ubuntu';folder.mkdir(parents=True,exist_ok=True)
    with tarfile.open(folder/'source.tar.gz','w:gz') as tar:
        for path,name in files('linux'):
            data=path.read_bytes()
            if path.suffix in ('.sh','.zsh','.service','.timer'):data=data.replace(b'\r\n',b'\n')
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if path.name=='nexsearch-vpn' or path.suffix in ('.sh','.zsh') else 0o644;tar.addfile(info,io.BytesIO(data))
        data=metadata('HostUbuntu');info=tarfile.TarInfo('package-info.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
    (folder/'install.sh').write_text((ROOT/'ubuntu/install.sh').read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    print('Prepared Ubuntu host',flush=True)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    windows('HostWindows','Хост');windows('EmployeeWindows','Сотрудник')
    for role,label in [('host','Хост'),('employee','Сотрудник')]:macos(role,label)
    linux()
    test=OUT/'Тест с этим компьютером/Windows';shutil.copytree(OUT/'Сотрудник/Windows',test,dirs_exist_ok=True)
    shutil.copy2(ROOT/'preview/host-data/identity/host-identity.json',test/'client-trust.json')
    test_mac=OUT/'Тест с этим компьютером/macOS';shutil.copytree(OUT/'Сотрудник/macOS',test_mac,dirs_exist_ok=True)
    app=next(test_mac.glob('*.app'));resources=app/'Contents/Resources';source=resources/'source.zip';pending=resources/'source-local.zip'
    public=(ROOT/'preview/host-data/identity/host-identity.json').read_bytes()
    with zipfile.ZipFile(source) as old,zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as new:
        for info in old.infolist():new.writestr(info,public if info.filename in ('client-trust.json','network/client-trust.json') else old.read(info))
    pending.replace(source);(resources/'build-id.txt').write_text('nexsearch-1.6.0-'+COMMIT[:12]+'-localtest')
    checks=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(resources).as_posix() for p in sorted(resources.rglob('*')) if p.is_file() and p.name!='checksums.txt']
    (resources/'checksums.txt').write_text('\n'.join(checks)+'\n',encoding='utf-8',newline='\n')
    (OUT/'Прочитать.txt').write_text('''NexSearch 1.6.0

Хост → Windows → NexSearch.exe — программа хоста. Рядом должен оставаться весь комплект папок.
Сотрудник → Windows → NexSearch.exe — программа сотрудника для офисного сервера.
На Mac откройте соответствующую папку macOS и Запустить NexSearch.command. Поддерживаются Intel и Apple Silicon, macOS 12 или новее. Физический запуск на Mac этой сборки пока не проверен.
На новом Ubuntu-сервере: откройте Хост/Linux Ubuntu и выполните sudo bash install.sh. Нужен интернет для установки системных пакетов. Существующий офисный хост обновляется через штатный подписанный канал, а не повторной установкой.

Тест с этим компьютером → Windows → NexSearch.exe (или папка macOS рядом) подключается к хосту, который сейчас запущен на компьютере пользователя. Это отдельный локальный хост; офисная база в комплект не переносилась. Для первого входа создайте сотрудника и подтвердите его на хосте.

В верхней панели выберите подключение:
Автоматически — в офисе локальная сеть, вне офиса VPN.
Локальная сеть — только локальная сеть.
VPN — только VPN, без обхода через локальную сеть, даже если компьютеры рядом. Выбор запоминается.

Для проверки VPN сначала в режиме локальной сети откройте Настройки → VPN. Авторизуйте хост и этот компьютер в одной VPN-сети организации. После этого выберите VPN в верхней панели. До авторизации соединение через VPN не готово; используйте показанные ссылки входа. В режиме VPN отключённый или неавторизованный хост не подменяется локальным соединением.

На флешке находятся программы и публичные профили подключения. Рабочая PostgreSQL, личные почтовые пароли и VPN-ключи устройств не копировались. GitHub и рабочий офисный сервер этой локальной сборкой автоматически не обновлялись.
''',encoding='utf-8')
    shutil.copy2(ROOT/'CHANGES-1.6.0.md',OUT/'Что нового.md')
    print('Offline kits ready: '+str(OUT),flush=True)

if __name__=='__main__':main()
