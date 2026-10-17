"""Reproducible application VPN builds. Requires Go from go.mod or newer."""
from pathlib import Path
import json,os,shutil,struct,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('windows-amd64','linux-amd64','darwin-amd64','darwin-arm64')

def macos_minimum(path):
    raw=Path(path).read_bytes()
    if raw[:4]!=b'\xcf\xfa\xed\xfe':raise RuntimeError('Expected 64-bit Mach-O')
    count=struct.unpack_from('<I',raw,16)[0];offset=32
    for _ in range(count):
        command,size=struct.unpack_from('<II',raw,offset)
        if command in (0x32,0x24):
            value=struct.unpack_from('<I',raw,offset+(12 if command==0x32 else 8))[0]
            return value>>16,(value>>8)&255,value&255
        offset+=size
    raise RuntimeError('Missing macOS minimum version')

def notices(go,destination,env,work):
    """Keep dependency licenses with redistributed native modules."""
    versions=subprocess.check_output([go,'version','-m',str(destination)],env=env,text=True)
    used={line.split()[1] for line in versions.splitlines() if line.strip().startswith('dep\t')}
    raw=subprocess.check_output([go,'list','-m','-json','all'],cwd=work,env=env,text=True)
    decoder=json.JSONDecoder();modules=[]
    while raw.strip():
        value,index=decoder.raw_decode(raw.lstrip());modules.append(value);raw=raw.lstrip()[index:]
    go_root=Path(subprocess.check_output([go,'env','GOROOT'],env=env,text=True).strip())
    sections=['Go standard library\n'+(go_root/'LICENSE').read_text(encoding='utf-8')]
    for module in modules:
        if module['Path'] not in used:continue
        directory=Path(module['Dir']);licenses=[p for p in directory.iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE','COPYING','NOTICE'))]
        if not licenses:raise RuntimeError('Missing dependency license: '+module['Path'])
        sections.append(module['Path']+' '+module.get('Version','')+'\n'+'\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in sorted(licenses)))
    (destination.parent/'THIRD-PARTY-NOTICES.txt').write_text('\n\n'+'\n\n'.join(sections),encoding='utf-8')

def build(target):
    if target not in TARGETS:raise ValueError('Unsupported platform: '+target)
    system,arch=target.split('-');env={**os.environ,'GOOS':system,'GOARCH':arch,'CGO_ENABLED':'0'}
    destination=ROOT/'network/bin'/target/('nexsearch-vpn.exe' if system=='windows' else 'nexsearch-vpn')
    destination.parent.mkdir(parents=True,exist_ok=True)
    go=os.environ.get('NEXSEARCH_GO','go')
    work=ROOT/'vpn'
    if system=='darwin':
        # Go 1.27 raised the minimum to macOS 13. Keep Monterey/MBP 2016
        # support with the maintained Go 1.26 toolchain and compatible tsnet.
        work=ROOT/'build/vpn-macos';work.mkdir(parents=True,exist_ok=True)
        for name in ('main.go','main_test.go'):shutil.copy2(ROOT/'vpn'/name,work/name)
        for name in ('go.mod','go.sum'):shutil.copy2(ROOT/'vpn/macos'/name,work/name)
        env['GOTOOLCHAIN']='go1.26.9'
    subprocess.run([go,'build','-trimpath','-ldflags=-s -w','-o',str(destination),'.'],cwd=work,env=env,check=True)
    if system=='darwin' and macos_minimum(destination)>(12,0,0):raise RuntimeError('This VPN build no longer supports Monterey')
    notices(go,destination,env,work)
    destination.chmod(0o755)
    print(target+': '+str(destination.stat().st_size)+' bytes',flush=True)

if __name__=='__main__':
    for target in sys.argv[1:] or TARGETS:build(target)
