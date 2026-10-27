"""Encrypt an operator bundle to the existing office TLS key; authenticate via release signing."""
from pathlib import Path
import argparse, base64, hashlib, io, json, os, tarfile
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGORITHM = 'P256-HKDF-SHA256-AES256GCM'
FIELDS = {'version', 'algorithm', 'host_id', 'fingerprint', 'ephemeral_key', 'nonce', 'ciphertext'}
MAX_ARCHIVE = 2 * 1024 * 1024
FILES = {'frpc.toml', 'ca.crt', 'tunnel.crt', 'tunnel.key', 'control.token',
         'install.py', 'frp-release.json', 'check.py', 'connect-office.sh',
         'verify-office.py', 'internet-access.json', 'README.txt'}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')

def b64(value):
    return base64.b64encode(value).decode('ascii')

def unb64(value):
    return base64.b64decode(value, validate=True)

def material(shared, fingerprint):
    return HKDF(algorithm=hashes.SHA256(), length=32,
                salt=bytes.fromhex(fingerprint), info=b'NexSearch office tunnel bootstrap v1').derive(shared)

def validate(payload):
    from uuid import UUID
    if set(payload) != FIELDS or payload['version'] != 1 or payload['algorithm'] != ALGORITHM:
        raise ValueError('Unsupported office envelope')
    UUID(payload['host_id'])
    fp = payload['fingerprint']
    if not isinstance(fp, str) or len(fp) != 64 or any(c not in '0123456789abcdef' for c in fp):
        raise ValueError('Invalid office fingerprint')
    if len(unb64(payload['nonce'])) != 12 or len(unb64(payload['ciphertext'])) > MAX_ARCHIVE + 16:
        raise ValueError('Invalid office envelope size')
    public = serialization.load_der_public_key(unb64(payload['ephemeral_key']))
    if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, ec.SECP256R1):
        raise ValueError('Expected P-256 ephemeral key')
    return public

def seal(archive, cert_pem, trust):
    if len(archive) > MAX_ARCHIVE:
        raise ValueError('Office archive too large; keep public FRP binaries outside encryption')
    certificate = x509.load_pem_x509_certificate(cert_pem)
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    if fingerprint != trust['fingerprint']:
        raise ValueError('Office public certificate does not match pinned identity')
    recipient = certificate.public_key()
    if not isinstance(recipient, ec.EllipticCurvePublicKey) or not isinstance(recipient.curve, ec.SECP256R1):
        raise ValueError('Expected office P-256 certificate')
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    nonce = os.urandom(12)
    header = {'version': 1, 'algorithm': ALGORITHM, 'host_id': trust['host_id'],
              'fingerprint': fingerprint, 'nonce': b64(nonce),
              'ephemeral_key': b64(ephemeral.public_key().public_bytes(
                  serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))}
    key = material(ephemeral.exchange(ec.ECDH(), recipient), fingerprint)
    payload = dict(header, ciphertext=b64(AESGCM(key).encrypt(nonce, archive, canonical(header))))
    validate(payload)
    return payload

def open_signed(envelope, signing_public_pem, cert_pem, private_pem, identity):
    if set(envelope) != {'payload', 'signature'}:
        raise ValueError('Expected a signed office envelope')
    payload = envelope['payload']
    signing_public = serialization.load_pem_public_key(signing_public_pem)
    signing_public.verify(unb64(envelope['signature']), canonical(payload))
    ephemeral = validate(payload)
    certificate = x509.load_pem_x509_certificate(cert_pem)
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    if payload['fingerprint'] != fingerprint or identity != {
            'host_id': payload['host_id'], 'fingerprint': fingerprint}:
        raise ValueError('This bundle belongs to another office host')
    private = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise ValueError('Expected office P-256 private key')
    if private.public_key().public_numbers() != certificate.public_key().public_numbers():
        raise ValueError('Office private key does not match the certificate')
    header = {k: v for k, v in payload.items() if k != 'ciphertext'}
    key = material(private.exchange(ec.ECDH(), ephemeral), fingerprint)
    return AESGCM(key).decrypt(unb64(payload['nonce']), unb64(payload['ciphertext']), canonical(header))

def extract(archive, target):
    """The encrypted archive contains only known regular files; no links or executable archive paths."""
    target = Path(target)
    if not target.is_dir() or any(target.iterdir()):
        raise ValueError('Expected an empty private output directory')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        members = tar.getmembers()
        expected = {'nexsearch-office-tunnel/' + name for name in FILES}
        if len(members) != len(expected) or {m.name for m in members} != expected:
            raise ValueError('Unexpected office bundle members')
        if any(not m.isfile() or m.size > 1024 * 1024 or m.size < 0 for m in members):
            raise ValueError('Unsafe office bundle member')
        for member in members:
            destination = target / member.name.split('/')[-1]
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output, tar.extractfile(member) as source:
                output.write(source.read())

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--envelope', type=Path, required=True)
    parser.add_argument('--identity', type=Path, default=Path('/var/lib/nexsearch/identity'))
    parser.add_argument('--signing-public', type=Path, default=Path('/etc/nexsearch/updates-public.pem'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    identity = json.loads((args.identity / 'host-identity.json').read_text())
    # Load only the three identity fields needed. No account/mail/GitHub credentials are used.
    identity = {key: identity[key] for key in ('host_id', 'fingerprint')}
    result = open_signed(json.loads(args.envelope.read_text()), args.signing_public.read_bytes(),
                         (args.identity / 'host-cert.pem').read_bytes(),
                         (args.identity / 'host-key.pem').read_bytes(), identity)
    extract(result, args.output)
    print('Office bundle signature and host identity verified; private bundle decrypted locally.')
