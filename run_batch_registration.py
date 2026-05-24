"""Batch Camoufox registration: 30 accounts via subprocess isolation."""

import io
import sys
import time
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.config.constants import EmailServiceType
from src.config.settings import get_settings
from src.core.register_v2 import RegistrationEngineV2
from src.database.session import init_database
from src.services import EmailServiceFactory


def run_one(index: int, email_service, proxy_url: str | None, settings) -> dict:
    logs = []

    engine = RegistrationEngineV2(
        email_service=email_service,
        proxy_url=proxy_url,
        browser_mode="camoufox",
        callback_logger=logs.append,
        max_retries=settings.registration_max_retries or 3,
    )
    result = engine.run()
    saved = engine.save_to_database(result) if result.success else False
    return {
        "index": index,
        "success": result.success,
        "email": result.email,
        "account_id": result.account_id,
        "error_message": result.error_message,
        "db_saved": saved,
        "logs": logs[-5:],
    }


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

    total = 30
    success = 0
    fail = 0
    results = []

    print(f"=== Batch Camoufox Registration: {total} accounts ===")
    print(f"Email service: {email_service.service_type.value}")

    for i in range(1, total + 1):
        print(f"\n[{i}/{total}] Starting...")
        try:
            r = run_one(i, email_service, None, settings)
        except Exception as e:
            r = {
                "index": i,
                "success": False,
                "email": "",
                "account_id": "",
                "error_message": str(e),
                "db_saved": False,
                "logs": [str(e)],
            }

        results.append(r)
        if r["success"]:
            success += 1
            print(f"[{i}/{total}] SUCCESS: {r['email']} -> {r['account_id']}")
        else:
            fail += 1
            error_msg = str(r.get("error_message", ""))[:120]
            print(f"[{i}/{total}] FAIL: {error_msg}")

        time.sleep(3)

    print(f"\nBatch complete: {success}/{total} succeeded")
    for r in results:
        if not r["success"]:
            print(f"  FAIL [{r['index']}]: {r.get('email','')} -> {r.get('error_message','')[:150]}")


if __name__ == "__main__":
    main()
