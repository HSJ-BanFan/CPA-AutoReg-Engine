"""Batch Camoufox registration: 50 accounts, serial execution."""

import io
import random
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.config.constants import EmailServiceType
from src.config.settings import get_settings
from src.core.register_v2 import RegistrationEngineV2
from src.database.session import init_database
from src.services import EmailServiceFactory


def main():
    init_database()
    settings = get_settings()

    imap_config = {
        "domain": "meilunaria.dpdns.org",
        "host": "imap.gmail.com",
        "port": 993,
        "use_ssl": True,
        "email": "maingocdangpha13101999@gmail.com",
        "password": "emnx vfil yxdy nuao",
        "folder": "INBOX",
        "timeout": 120,
        "poll_interval": 5.0,
        "mail_limit": 20,
    }
    print(f"Initializing IMAP mail with {imap_config['email']}...")
    email_service = EmailServiceFactory.create(
        EmailServiceType.IMAP_MAIL, imap_config
    )

    total = 50
    success = 0
    fail = 0
    results = []

    print(f"=== Batch Camoufox Registration (serial): {total} accounts ===")
    print(f"Email service: {email_service.service_type.value}")

    for i in range(1, total + 1):
        print(f"\n[{i}/{total}] Starting...")

        engine = RegistrationEngineV2(
            email_service=email_service,
            proxy_url=None,
            browser_mode="camoufox",
            callback_logger=lambda msg: print(f"  [{i}] {msg}"),
            max_retries=settings.registration_max_retries or 3,
        )
        try:
            result = engine.run()
        except Exception as e:
            result = type("R", (), {"success": False, "email": "", "account_id": "", "error_message": str(e)})()

        saved = engine.save_to_database(result) if result.success else False
        r = {
            "index": i,
            "success": result.success,
            "email": result.email,
            "account_id": result.account_id,
            "error_message": result.error_message,
            "db_saved": saved,
        }
        results.append(r)

        if r["success"]:
            success += 1
            print(f"[{i}/{total}] SUCCESS: {r['email']} -> {r['account_id']}")
        else:
            fail += 1
            print(f"[{i}/{total}] FAIL: {str(r.get('error_message', ''))[:120]}")

        time.sleep(random.uniform(2, 5))

    print(f"\nBatch complete: {success}/{total} succeeded")
    for r in results:
        if not r["success"]:
            print(f"  FAIL [{r['index']}]: {r.get('email','')} -> {r.get('error_message','')[:150]}")


if __name__ == "__main__":
    main()
