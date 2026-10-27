"""CI: sign the encrypted office bootstrap; never holds its plaintext credentials."""
from pathlib import Path
import argparse, base64, hashlib, io, json, os, sys, tempfile, urllib.request, zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from envelope import canonical, validate, b64

def build(output, commit):
    payload = json.loads((ROOT / 'deployment/office-tunnel.enc.json').read_text())
    validate(payload)
    trust = json.loads((ROOT / 'network/client-trust.json').read_text())
    if any(payload[key] != trust[key] for key in ('host_id', 'fingerprint')):
        raise ValueError('Encrypted bundle is for another host')
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['NEXSEARCH_SIGNING_KEY']))
    signed = {'payload': payload, 'signature': b64(key.sign(canonical(payload)))}
    release = json.loads((HERE / 'frp-release.json').read_text())
    version = release['version']
    archive_name = f'frp_{version}_linux_amd64.tar.gz'
    with urllib.request.urlopen('https://github.com/fatedier/frp/releases/download/' +
                                f'v{version}/{archive_name}', timeout=60) as response:
        archive = response.read(64 * 1024 * 1024 + 1)
    if len(archive) > 64 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != release['linux_amd64']:
        raise ValueError('FRP download checksum mismatch')
    files = {'office-tunnel.signed.json': canonical(signed), archive_name: archive,
             'envelope.py': (HERE / 'envelope.py').read_bytes(),
             'connect-office.sh': (HERE / 'connect-release.sh').read_bytes(),
             'README.txt': (HERE / 'RELEASE-README.txt').read_bytes()}
    files = {name: data.replace(b'\r\n', b'\n') if name.endswith(('.sh', '.py')) else data
             for name, data in files.items()}
    info = json.loads((ROOT / 'version.json').read_text())
    manifest = {'version': info['version'], 'schema': info['schema'], 'source_commit': commit,
                'role': 'OfficeTunnel', 'files': {'OfficeTunnel/' + name: hashlib.sha256(data).hexdigest()
                                                 for name, data in sorted(files.items())}}
    raw = canonical(manifest)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('release.json', raw)
        z.writestr('release.sig', key.sign(raw))
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo('OfficeTunnel/' + name)
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(entry, data)
    # Text normalization must be reflected in manifest hashes.
    with zipfile.ZipFile(output) as z:
        for name, digest in manifest['files'].items():
            if hashlib.sha256(z.read(name)).hexdigest() != digest:
                raise ValueError('Text newline mismatch in release: ' + name)
    print(json.dumps({'role': 'OfficeTunnel', 'version': info['version'], 'private_plaintext_published': False,
                      'bytes': output.stat().st_size}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    build(args.output, args.commit)
