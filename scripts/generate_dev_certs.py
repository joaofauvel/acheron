"""Generate a local Acheron CA and per-service dev certs.

Generation is non-destructive for existing certificate material. A complete
marked development bundle is reused unless ``--force`` is supplied.

Usage:
    uv run python scripts/generate_dev_certs.py [--out-dir ./certs] [--force]
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import shutil
import tempfile
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

DEV_CA_MARKER = ".dev-ca"
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
    path.chmod(0o600)


def _write_pem_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o644)


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


def _managed_paths(out_dir: Path) -> tuple[Path, ...]:
    """Return all files owned by a generated development bundle."""
    return (
        out_dir / "acheron-ca.crt",
        out_dir / "acheron-ca.key",
        *(out_dir / f"{service}.{suffix}" for service in SERVICES for suffix in ("crt", "key")),
    )


def _preflight(out_dir: Path) -> str:
    """Classify the output directory before generating any material."""
    marker = out_dir / DEV_CA_MARKER
    managed = _managed_paths(out_dir)
    present = tuple(path for path in managed if path.is_file())
    if not marker.exists() and not present:
        return "fresh"
    if marker.is_file() and len(present) == len(managed):
        return "complete-marked"
    if marker.exists():
        missing = ", ".join(path.name for path in managed if not path.is_file())
        message = (
            "marked development certificate bundle is incomplete; "
            f"missing {missing}. Remove the partial development material and retry. "
            "--force is only allowed for a complete marked bundle."
        )
        raise RuntimeError(message)
    raise RuntimeError(
        "unmarked certificate material already exists; refusing to overwrite it. "
        "Remove the existing development material or pass --force only after "
        "creating a complete marked development bundle."
    )


def _write_marker(path: Path) -> None:
    """Mark a fully generated bundle as development-owned."""
    path.write_text("Acheron development certificate bundle.\n", encoding="utf-8")
    path.chmod(0o644)


def _publish_bundle(staging_dir: Path, out_dir: Path) -> None:
    """Publish a complete bundle while preserving the previous bundle on failure."""
    destinations = (*_managed_paths(out_dir), out_dir / DEV_CA_MARKER)
    backup_dir = Path(tempfile.mkdtemp(prefix=".dev-ca-backup-", dir=out_dir))
    published: list[Path] = []
    try:
        for destination in destinations:
            if destination.is_file():
                shutil.copy2(destination, backup_dir / destination.name)
        try:
            for staged_path in (*_managed_paths(staging_dir), staging_dir / DEV_CA_MARKER):
                destination = out_dir / staged_path.name
                staged_path.replace(destination)
                published.append(destination)
        except OSError:
            for destination in reversed(published):
                backup = backup_dir / destination.name
                if backup.is_file():
                    backup.rename(destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def generate(out_dir: Path, *, force: bool = False) -> bool:
    """Generate the Acheron CA and per-service certs in `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state = _preflight(out_dir)
    if state == "complete-marked" and not force:
        return False

    # World-executable dir so workers can `ls` and `stat` files; file-level
    # permissions (0600 for keys, 0644 for certs) are set at write time.
    out_dir.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix=".dev-ca-", dir=out_dir) as staging:
        staging_dir = Path(staging)
        ca_cert, ca_key = _build_ca(staging_dir)
        for service in SERVICES:
            _build_server_cert(service, staging_dir, ca_cert, ca_key)
        _write_marker(staging_dir / DEV_CA_MARKER)
        _publish_bundle(staging_dir, out_dir)
    return True


def main() -> None:
    """Entry point: parse args, generate certs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("certs"),
        help="Output directory for certs (default: ./certs)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate a complete marked development bundle",
    )
    args = parser.parse_args()
    try:
        generated = generate(args.out_dir, force=args.force)
    except RuntimeError as exc:
        parser.error(str(exc))
    if generated:
        print(f"Generated Acheron CA and {len(SERVICES)} service certs in {args.out_dir}")  # noqa: T201
    else:
        print(f"Reused existing Acheron development certificate bundle in {args.out_dir}")  # noqa: T201


if __name__ == "__main__":
    main()
