from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib
import ipaddress
import socket
from uuid import uuid4
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from . import secure_store

def identity(home):
    home=Path(home);home.mkdir(parents=True,exist_ok=True)
    metadata=home/'host-identity.json';cert_file=home/'host-cert.pem';key_file=home/'host-key.pem'
    if not metadata.exists():
        import json
        host_id=str(uuid4());key=ec.generate_private_key(ec.SECP256R1())
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Nexol '+host_id)])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=3650))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.DNSName(socket.gethostname()),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
              .sign(key,hashes.SHA256()))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        if __import__('os').name!='nt':key_file.chmod(0o600)
        metadata.write_text(json.dumps({'host_id':host_id,'fingerprint':cert.fingerprint(hashes.SHA256()).hex()}),encoding='utf-8')
    import json
    data=json.loads(metadata.read_text(encoding='utf-8'))
    cert=x509.load_pem_x509_certificate(cert_file.read_bytes())
    if cert.fingerprint(hashes.SHA256()).hex()!=data['fingerprint']:raise ValueError('Сертификат хоста не совпадает с его идентификатором')
    return data,cert_file,key_file
