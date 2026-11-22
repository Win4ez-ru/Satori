"""Explicit local TLS identity. No listener, network discovery or trust bypass."""

import hashlib
import ipaddress
import os
import re
import ssl
import subprocess
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .contracts import GatewayFault
from .private_store import PrivateStore


def advertised_host(value: str) -> str:
    """Accept an exact ASCII DNS name or IP, never a URL, wildcard or authority."""
    if not isinstance(value, str) or not value or len(value) > 253 or not value.isascii():
        raise GatewayFault("invalid_request")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if value.replace(".", "").isdigit():
            raise GatewayFault("invalid_request") from None
        if value != value.lower() or value.endswith("."):
            raise GatewayFault("invalid_request") from None
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ):
            raise GatewayFault("invalid_request") from None
        return value
    if address.is_unspecified or address.is_multicast:
        raise GatewayFault("invalid_request")
    return str(address)


def allowed_bind(value: str, *, lan_enabled: bool) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise GatewayFault("invalid_request") from None
    if address.is_loopback:
        return str(address)
    private_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("fc00::/7"),
    )
    if lan_enabled and any(address.version == n.version and address in n for n in private_networks):
        return str(address)
    raise GatewayFault("forbidden", 403)


def authority(host: str, port: int) -> str:
    host = advertised_host(host)
    if type(port) is not int or not 1 <= port <= 65535:
        raise GatewayFault("invalid_request")
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass(frozen=True)
class TLSIdentity:
    certificate: Path
    private_key: Path
    host: str
    sha256: str

    def transport_id(self, port: int) -> str:
        return authority(self.host, port) + "/" + self.sha256

    def server_context(self) -> ssl.SSLContext:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.options |= ssl.OP_NO_COMPRESSION
            context.load_cert_chain(str(self.certificate), str(self.private_key))
            return context
        except (OSError, ValueError, ssl.SSLError):
            raise GatewayFault("operation_failed", 503) from None


def _openssl(arguments: list[str]) -> None:
    try:
        subprocess.run(
            ["/usr/bin/openssl", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=20,
            umask=0o077,
        )
    except (OSError, subprocess.SubprocessError):
        raise GatewayFault("operation_failed", 503) from None


def _verify_certificate(certificate: Path, key: Path, host: str) -> None:
    """Independently inspect an existing leaf, not only its metadata descriptor."""
    try:
        if certificate.stat().st_size > 16384 or key.stat().st_size > 16384:
            raise GatewayFault("operation_failed", 503)
        result = subprocess.run(
            ["/usr/bin/openssl", "x509", "-in", str(certificate), "-noout", "-text"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
        if len(result.stdout) > 65536:
            raise GatewayFault("operation_failed", 503)
        text = result.stdout.decode("ascii")
        dates = re.findall(
            r"Not (Before|After)\s*:\s*([A-Za-z]{3})\s+(\d{1,2}) "
            r"(\d{2}):(\d{2}):(\d{2}) (\d{4}) GMT",
            text,
        )
        if len(dates) != 2:
            raise GatewayFault("operation_failed", 503)
        months = {
            name: index
            for index, name in enumerate(
                (
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ),
                1,
            )
        }
        bounds = {
            name: datetime(
                int(year), months[month], int(day), int(hour), int(minute), int(second), tzinfo=UTC
            )
            for name, month, day, hour, minute, second, year in dates
        }
        if not bounds["Before"] <= datetime.now(UTC) < bounds["After"]:
            raise GatewayFault("operation_failed", 503)
        san_match = re.search(r"X509v3 Subject Alternative Name:\s*\n\s*([^\n]+)", text)
        if san_match is None:
            raise GatewayFault("operation_failed", 503)
        entries = [entry.strip() for entry in san_match.group(1).split(",")]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            valid = entries == ["DNS:" + host]
        else:
            valid = (
                len(entries) == 1
                and entries[0].startswith("IP Address:")
                and ipaddress.ip_address(entries[0].split(":", 1)[1]) == address
            )
        if not valid:
            raise GatewayFault("operation_failed", 503)
    except (OSError, ValueError, UnicodeError, KeyError, subprocess.SubprocessError):
        raise GatewayFault("operation_failed", 503) from None


def prepare_tls(store: PrivateStore, host: str, *, rotate: bool = False) -> TLSIdentity:
    """Called only by an explicit owner setting. Rotating requires re-pairing.

    Certificates carry the advertised endpoint's exact SAN. The bind address is separate.
    Existing incomplete, changed or expired identity material fails closed, not reset.
    """
    host = advertised_host(host)
    certificate = store.path("server-cert.pem")
    key = store.path("server-key.pem")
    metadata = store.read_json("tls-identity.json")
    if not rotate and any((certificate.exists(), key.exists(), metadata is not None)):
        if not certificate.exists() or not key.exists() or metadata is None:
            raise GatewayFault("operation_failed", 503)
        if metadata.get("host") != host:
            raise GatewayFault("profile_changed", 409)
        _verify_certificate(certificate, key, host)
        try:
            der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
            pin = hashlib.sha256(der).hexdigest()
        except (OSError, ValueError, UnicodeError):
            raise GatewayFault("operation_failed", 503) from None
        if metadata != {"schema_version": 1, "host": host, "sha256": pin}:
            raise GatewayFault("operation_failed", 503)
        _openssl(["x509", "-in", str(certificate), "-noout", "-checkend", "0"])
        result = TLSIdentity(certificate, key, host, pin)
        result.server_context()  # Verify key/certificate correspondence before admission.
        return result

    nonce = uuid.uuid4().hex
    config = store.path(f"tls-{nonce}.cnf")
    staged_key = store.path(f"tls-{nonce}.key")
    staged_cert = store.path(f"tls-{nonce}.crt")
    try:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            san = "DNS:" + host
        else:
            san = "IP:" + host
        content = (
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
            "[dn]\nCN=Satori local mobile\n[ext]\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\nsubjectAltName=" + san + "\n"
        )
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        _openssl(
            [
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-days",
                "30",
                "-keyout",
                str(staged_key),
                "-out",
                str(staged_cert),
                "-config",
                str(config),
            ]
        )
        store.path(staged_key.name)
        store.path(staged_cert.name)
        if staged_cert.stat().st_size > 16384 or staged_key.stat().st_size > 16384:
            raise GatewayFault("operation_failed", 503)
        pin = hashlib.sha256(
            ssl.PEM_cert_to_DER_cert(staged_cert.read_text(encoding="ascii"))
        ).hexdigest()
        for path in (staged_key, staged_cert):
            with path.open("rb") as file:
                os.fsync(file.fileno())
        os.replace(staged_key, key)
        os.replace(staged_cert, certificate)
        _verify_certificate(certificate, key, host)
        store.write_json("tls-identity.json", {"schema_version": 1, "host": host, "sha256": pin})
        result = TLSIdentity(certificate, key, host, pin)
        result.server_context()
        return result
    except (OSError, ValueError, UnicodeError):
        raise GatewayFault("operation_failed", 503) from None
    finally:
        for path in (config, staged_key, staged_cert):
            with suppress(OSError):
                path.unlink(missing_ok=True)
