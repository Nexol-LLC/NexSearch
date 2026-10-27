"""Install the signed office connector already fetched by the existing host updater."""
from pathlib import Path
import json, os, subprocess, sys, tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from network.updates import prepare

def install_from_cache(program, cache, public, work):
    archive = work / 'office-connect.zip'
    archive.write_bytes(subprocess.check_output([
        'git', '--git-dir', str(cache), 'show',
        'refs/remotes/origin/host:.updates/host-office-connect.zip']))
    archive.chmod(0o600)
    result = prepare(archive, public, work, 'OfficeTunnel')
    stage = Path(result['state_file']).parent
    manifest = json.loads((stage / 'release.json').read_text())
    installed = json.loads((program / 'package-info.json').read_text())
    if manifest['source_commit'] != installed['source_commit'] or manifest['version'] != installed['version']:
        raise RuntimeError('Пакет подключения не совпадает с установленной версией хоста. Дождитесь завершения nexsearch-update.service и повторите команду.')
    subprocess.run(['bash', str(stage / 'connect-office.sh')], check=True)

if __name__ == '__main__':
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Запустите эту команду через sudo на офисном Ubuntu-хосте NexSearch')
    with tempfile.TemporaryDirectory(prefix='nexsearch-office-connect-', dir='/var/tmp') as temporary:
        install_from_cache(Path('/opt/nexsearch/current'), Path('/var/lib/nexsearch-updater/repo.git'),
                           Path('/etc/nexsearch'), Path(temporary))
