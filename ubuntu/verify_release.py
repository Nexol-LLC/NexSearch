"""Verify a kit against an already trusted public key before installing."""
from pathlib import Path
import hashlib,json,sys
from cryptography.hazmat.primitives.serialization import load_pem_public_key
root=Path(sys.argv[1]).resolve();key=Path(sys.argv[2]);raw=(root/'release.json').read_bytes()
load_pem_public_key(key.read_bytes()).verify((root/'release.sig').read_bytes(),raw)
manifest=json.loads(raw)
if manifest.get('role')!='HostUbuntu' or manifest.get('schema') not in (14,15):raise ValueError('Incompatible host kit')
for name,digest in manifest['files'].items():
 p=(root/name).resolve()
 if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Invalid release file')
print('Verified version',manifest['version'])
