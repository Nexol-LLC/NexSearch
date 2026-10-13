"""Install packaged wheels without pip, network access, or system Python."""
from pathlib import Path,PurePosixPath
import os,sys,zipfile,shutil

def extract_archive(archive,target):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            relative=PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe package entry')
            destination=(target/Path(*relative.parts)).resolve()
            if not destination.is_relative_to(target):raise ValueError('Unsafe package entry')
            if entry.is_dir():destination.mkdir(parents=True,exist_ok=True);continue
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(z.read(entry))
            mode=(entry.external_attr>>16)&0o777
            destination.chmod(mode or 0o600)

def install(package,runtime,arch):
    package=Path(package);runtime=Path(runtime)
    extract_archive(package/'source.zip',runtime/'source')
    deps=runtime/'dependencies';deps.mkdir(exist_ok=True)
    for wheel in sorted((package/'Wheels'/arch).glob('*.whl')):extract_archive(wheel,deps)
    if not (deps/'requests').exists() or not (deps/'zeroconf').exists():raise RuntimeError('Не найдены зависимости программы')
    if sys.platform=='darwin':
        import subprocess
        desktop=runtime/'source/desktop/runtime';desktop.mkdir(parents=True,exist_ok=True)
        subprocess.run(['/usr/bin/ditto','-x','-k',str(package/'Runtimes'/('electron-'+arch+'.zip')),str(desktop)],check=True)
    print('Offline source and dependencies prepared.')

if __name__=='__main__':install(*sys.argv[1:4])
