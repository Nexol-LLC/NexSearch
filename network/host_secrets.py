"""Portable host-only secrets sealed with its private TLS identity."""
from pathlib import Path
import hashlib,json,os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
def secret_path():return Path(os.environ.get('NEXSEARCH_SECRET_FILE','host-secrets.enc'))
def key_path():return Path(os.environ.get('NEXSEARCH_KEY_FILE','host-key.pem'))
def load(path=None,key_file=None):
 p=Path(path or secret_path());key_file=Path(key_file or key_path())
 if not p.exists():return {}
 raw=p.read_bytes()
 if not raw.startswith(b'NXSSEC13'):raise ValueError('Повреждены защищённые настройки хоста')
 key=hashlib.sha256(key_file.read_bytes()+b'NexSearch host-only secrets 1.3').digest()
 return json.loads(AESGCM(key).decrypt(raw[8:20],raw[20:],b'NXSSEC13'))
def save(value,path=None,key_file=None):
 p=Path(path or secret_path());key_file=Path(key_file or key_path());p.parent.mkdir(parents=True,exist_ok=True)
 key=hashlib.sha256(key_file.read_bytes()+b'NexSearch host-only secrets 1.3').digest();nonce=os.urandom(12)
 p.write_bytes(b'NXSSEC13'+nonce+AESGCM(key).encrypt(nonce,json.dumps(value).encode(),b'NXSSEC13'))
 if os.name!='nt':p.chmod(0o600)
