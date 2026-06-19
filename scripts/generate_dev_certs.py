"""Generate a local Acheron CA and per-service dev certs.

Idempotent. Re-running overwrites existing certs in the output directory.

Usage:
    uv run python scripts/generate_dev_certs.py [--out-dir ./certs]
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SERVICES = [
    "orchestrator",
    "tts-stub",
    "asr-stub",
    "translation-stub",
    "tts-grpc-stub",
]

CA_CN = "Acheron Dev CA"
VALIDITY_DAYS = 365
KEY_SIZE = 2048


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def _write_pem_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_pem_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _build_ca(out_dir: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _generate_key()
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, CA_CN),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Acheron"),
        ]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                data_encipherment=False,
                content_commitment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    _write_pem_cert(out_dir / "acheron-ca.crt", cert)
    _write_pem_key(out_dir / "acheron-ca.key", key)
    return cert, key


def _build_server_cert(
    service: str,
    out_dir: Path,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
) -> None:
    key = _generate_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(service),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_pem_cert(out_dir / f"{service}.crt", cert)
    _write_pem_key(out_dir / f"{service}.key", key)


def generate(out_dir: Path) -> None:
    """Generate the Acheron CA and per-service certs in `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = _build_ca(out_dir)
    for service in SERVICES:
        _build_server_cert(service, out_dir, ca_cert, ca_key)


def main() -> None:
    """Entry point: parse args, generate certs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("certs"),
        help="Output directory for certs (default: ./certs)",
    )
    args = parser.parse_args()
    generate(args.out_dir)
    print(f"Generated Acheron CA and {len(SERVICES)} service certs in {args.out_dir}")  # noqa: T201


if __name__ == "__main__":
    main()
