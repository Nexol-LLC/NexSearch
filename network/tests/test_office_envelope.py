"""Release bootstrap confidentiality and identity/signature boundary tests; no production keys."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import importlib.util, io, json, tarfile, tempfile, unittest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

spec = importlib.util.spec_from_file_location('office_envelope', Path(__file__).resolve().parents[2] / 'ubuntu/tunnel/envelope.py')
envelope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envelope)

def identity():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'isolated office envelope test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM),
            {'host_id': str(uuid4()), 'fingerprint': cert.fingerprint(hashes.SHA256()).hex()})

def archive(extra=None):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as tar:
        for name in sorted(envelope.FILES | ({extra} if extra else set())):
            raw = ('fixture for ' + name).encode()
            member = tarfile.TarInfo('nexsearch-office-tunnel/' + name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
    return data.getvalue()

class OfficeEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.private, self.cert, self.identity = identity()
        self.signing = ed25519.Ed25519PrivateKey.generate()
        self.public = self.signing.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.raw = archive()
        self.payload = envelope.seal(self.raw, self.cert, self.identity)
        self.signed = {'payload': self.payload, 'signature': envelope.b64(self.signing.sign(envelope.canonical(self.payload)))}
    def open(self, signed=None, private=None, cert=None, host=None, public=None):
        return envelope.open_signed(signed or self.signed, public or self.public, cert or self.cert,
                                    private or self.private, host or self.identity)
    def test_roundtrip_and_safe_private_extraction(self):
        self.assertEqual(self.open(), self.raw)
        with tempfile.TemporaryDirectory() as folder:
            envelope.extract(self.open(), folder)
            self.assertEqual({p.name for p in Path(folder).iterdir()}, envelope.FILES)
            with self.assertRaises(ValueError):
                envelope.extract(self.raw, folder)
        self.assertNotIn('fixture for control.token', json.dumps(self.signed))
    def test_wrong_signer_rejected_before_decryption(self):
        wrong = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(InvalidSignature):
            self.open(public=wrong)
    def test_changed_payload_rejected(self):
        modified = json.loads(json.dumps(self.signed))
        modified['payload']['host_id'] = str(uuid4())
        with self.assertRaises(InvalidSignature):
            self.open(signed=modified)
    def test_different_host_or_private_key_rejected(self):
        private, cert, host = identity()
        with self.assertRaises(ValueError):
            self.open(private=private, cert=cert, host=host)
        with self.assertRaises(ValueError):
            self.open(private=private)
    def test_wrong_certificate_not_used_for_encryption(self):
        _, cert, _ = identity()
        with self.assertRaises(ValueError):
            envelope.seal(self.raw, cert, self.identity)
    def test_unsafe_archive_members_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                envelope.extract(archive('../../outside'), folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

if __name__ == '__main__':
    unittest.main()
