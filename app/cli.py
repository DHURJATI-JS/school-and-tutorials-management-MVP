"""Admin CLI.

    python -m app.cli hash-password            # bcrypt hash for ADMIN_USERS in .env
    python -m app.cli build-assets             # minify + fingerprint CSS/JS
    python -m app.cli wordlist-export f.json   # decode the moderation word lists for editing
    python -m app.cli wordlist-import f.json   # re-encode them (delete f.json afterwards!)
    python -m app.cli make-cert                # self-signed HTTPS cert for LAN use (camera/mic need HTTPS)
    python -m app.cli check                    # test DB, AI model, OCR and SMTP connectivity
    python -m app.cli demo-data [--students 36] [--with-insights]   # realistic demo school (password Demo@12345)
    python -m app.cli demo-data --clear        # remove exactly the demo data again
"""
from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path


def hash_password() -> None:
    from .security import hash_password as _hash

    pwd = getpass.getpass("Password: ")
    if len(pwd) < 8:
        sys.exit("Use at least 8 characters.")
    if pwd != getpass.getpass("Repeat: "):
        sys.exit("Passwords don't match.")
    print(_hash(pwd))


def make_cert() -> None:
    import datetime as dt
    import ipaddress
    import socket

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        sys.exit("pip install cryptography   (or create a certificate with mkcert / openssl)")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = socket.gethostname()
    ips = {"127.0.0.1"}
    try:
        ips.add(socket.gethostbyname(host))
    except OSError:
        pass
    names = [x509.DNSName("localhost"), x509.DNSName(host)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ClassNest LAN")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(names), critical=False).sign(key, hashes.SHA256()))
    out = Path("certs")
    out.mkdir(exist_ok=True)
    (out / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
    (out / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print("Created certs/cert.pem and certs/key.pem for", ", ".join(sorted(ips)))
    print("Set in .env:  SSL_CERTFILE=certs/cert.pem  SSL_KEYFILE=certs/key.pem  SESSION_COOKIE_SECURE=true")


async def check() -> None:
    from sqlalchemy import text

    from .database import engine
    from .services.ai import ai
    from .services.ocr import ocr_health

    try:
        async with engine.connect() as conn:
            version = await conn.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        print("PostgreSQL ......... OK (pgvector", version or "not installed yet", ")")
    except Exception as exc:
        print("PostgreSQL ......... FAILED:", exc.__class__.__name__, exc)
    info = await ai.health()
    print("AI gateway ......... ", "OK " + ", ".join(info["models"][:5]) if info["reachable"]
          else f"UNREACHABLE ({info.get('error')}) - AI features will use fallbacks")
    ocr = await ocr_health()
    print("OCR service ........ ", "OK" if ocr.get("reachable") else f"UNREACHABLE ({ocr.get('error')})")
    await engine.dispose()


async def demo_data(args: list[str]) -> None:
    from sqlalchemy import select

    from .cache import site_cache
    from .database import SessionLocal, engine, init_db
    from .models import User
    from .services import background, storage
    from .services.demo import DEMO_PASSWORD, DEMO_TAG, clear_demo, create_demo, demo_exists

    students = 36
    if "--students" in args:
        try:
            students = max(3, min(300, int(args[args.index("--students") + 1])))
        except (IndexError, ValueError):
            sys.exit("Usage: python -m app.cli demo-data --students 36")
    await init_db()
    storage.ensure_dirs()
    await site_cache.refresh()
    async with SessionLocal() as db:
        if "--clear" in args:
            print("Removed demo data:", await clear_demo(db))
        elif await demo_exists(db):
            print("Demo data is already loaded. Remove it first with:  python -m app.cli demo-data --clear")
        else:
            counts = await create_demo(db, students=students, log=lambda m: print("  " + m))
            print("Created:", ", ".join(f"{v} {k}" for k, v in counts.items()))
            if "--with-insights" in args:
                from .services.performance import analyze_all

                print("  Running the AI performance analysis (alerts + e-mails to the demo parents/teachers and the "
                      "admin alert address)...")
                print(f"  {await analyze_all(db)} performance alerts created")
            sample = (await db.scalars(select(User).where(User.created_by == DEMO_TAG)
                                       .order_by(User.role.desc(), User.id))).all()
            pick = {}
            for u in sample:
                pick.setdefault(u.staff_role or u.role, u)
            print(f"\nSign in at /login with any demo ID and the password  {DEMO_PASSWORD}  (demo e-mails: "
                  "@demo.classnest.test, never delivered)")
            for key in ("student", "teacher", "principal", "coordinator", "cleaner"):
                if key in pick:
                    u = pick[key]
                    print(f"  {key:<12} {u.id_code}  {u.full_name:<22} username: {u.username}")
            print("\nRemove everything again with:  python -m app.cli demo-data --clear")
    await background.drain(60)
    await engine.dispose()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "hash-password":
        hash_password()
    elif cmd == "build-assets":
        from .assets import build_assets

        print(json.dumps(build_assets(), indent=2))
    elif cmd == "wordlist-export" and len(sys.argv) > 2:
        from .services.moderation import export_wordlists

        Path(sys.argv[2]).write_text(json.dumps(export_wordlists(), indent=2), encoding="utf-8")
        print("Exported. Edit, then run wordlist-import and DELETE the plain file.")
    elif cmd == "wordlist-import" and len(sys.argv) > 2:
        from .services.moderation import import_wordlists

        import_wordlists(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        print("Encoded into app/data/wordlists.dat - now delete", sys.argv[2])
    elif cmd == "make-cert":
        make_cert()
    elif cmd == "check":
        asyncio.run(check())
    elif cmd == "demo-data":
        asyncio.run(demo_data(sys.argv[2:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
