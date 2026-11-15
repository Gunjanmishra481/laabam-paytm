"""Serve the app over https on the local Wi-Fi, so a phone can try it (with the mic) during a demo.

    cd backend && source .venv/Scripts/activate && python -m scripts.serve_lan
    python -m scripts.serve_lan --port 8443 --ip 192.168.1.128

Then open the printed https://<this PC's IP>:8443 on a phone on the same Wi-Fi. Chrome warns once that the
certificate isn't from a known authority; tap Advanced, then Proceed. After that the page is a secure context, so
the microphone works and the sign-in cookie sticks across reloads.

- Keys: backend/.env is loaded if present (KEY=value lines; values already in the environment win). Never commit it.
- Sandbox demo: LAABAM_SANDBOX=1 unless set otherwise. Development only: refuses to run with LAABAM_ENV demo/prod.
- Certificate: a self-signed P-256 certificate for this PC's IP, valid 7 days, made on first run (and again when it
  expires or the IP changes) in backend/var/lan-tls/, which git ignores. Never commit it.
- Windows: the default asyncio loop (Proactor) stops accepting connections for good after WinError 64, which a phone
  causes by dropping a connection mid-handshake (screen lock, Wi-Fi switch). This runs uvicorn on the selector loop,
  which just drops that one connection, and restarts the server if it ever stops on an error. Ctrl+C stops it.
- Anyone on the same Wi-Fi can open it, and live voice and bill reads spend your Sarvam credit. Stop it when done.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import ipaddress
import os
import socket
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TLS_DIR = BACKEND / "var" / "lan-tls"
CERT_DAYS = 7


def load_env_file(path: Path) -> list[str]:
    """KEY=value lines into os.environ (without overriding). Returns the names loaded, never the values."""
    loaded = []
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def lan_ip() -> str:
    """This PC's address on the local network (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


def ensure_cert(ip: str) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = TLS_DIR / "cert.pem", TLS_DIR / "key.pem"
    now = datetime.datetime.now(datetime.UTC)
    if cert_path.is_file() and key_path.is_file():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        fresh = cert.not_valid_after_utc - now > datetime.timedelta(hours=1)
        if fresh and ipaddress.ip_address(ip) in sans.get_values_for_type(x509.IPAddress):
            return cert_path, key_path
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Laabam LAN demo")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip)),
                                                        x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"made a new {CERT_DAYS}-day certificate for {ip} (phones will warn once again)", flush=True)
    return cert_path, key_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--ip", help="this PC's Wi-Fi address (found automatically if left out)")
    args = ap.parse_args()

    loaded = load_env_file(BACKEND / ".env")
    os.environ.setdefault("LAABAM_SANDBOX", "1")
    if os.environ.get("LAABAM_ENV", "dev") != "dev":
        sys.exit("serve_lan is for development only (LAABAM_ENV must be dev)")
    ip = args.ip or lan_ip()
    cert, key = ensure_cert(ip)
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)

    import uvicorn

    print(f"loaded from backend/.env: {', '.join(loaded) or 'nothing'}", flush=True)
    print(f"open on the phone: https://{ip}:{args.port}   (demo view: https://{ip}:{args.port}/demo)", flush=True)
    while True:
        config = uvicorn.Config("laabam.app:app", host="0.0.0.0", port=args.port, loop="none",
                                ssl_certfile=str(cert), ssl_keyfile=str(key))
        server = uvicorn.Server(config)
        try:
            if sys.platform == "win32":
                asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
            else:
                asyncio.run(server.serve())
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001 - keep the demo up; the error is printed
            print(f"server stopped on an error ({type(e).__name__}: {e}); restarting in 2 s", flush=True)
            time.sleep(2)
            continue
        if server.should_exit:  # Ctrl+C or a clean shutdown
            break


if __name__ == "__main__":
    main()
