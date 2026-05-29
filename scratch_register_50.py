import io
import sys
import time
import os

# Set encoding to prevent print errors
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pydantic.types import SecretStr
from src.config.constants import EmailServiceType
from src.config.settings import get_settings
from src.core.register_v2 import RegistrationEngineV2
from src.database.session import init_database
from src.services import EmailServiceFactory

def main():
    init_database()
    settings = get_settings()
    
    # Configure webchat2api settings properly with SecretStr
    settings.webchat2api_enabled = True
    settings.webchat2api_base_url = "http://127.0.0.1:83"
    settings.webchat2api_api_token = SecretStr("admin")

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
    
    # Use proxy from settings or fallback to port 7897
    proxy_url = settings.proxy_url or "http://127.0.0.1:7897"
    print(f"Using proxy URL: {proxy_url}")
    print(f"Using browser mode: camoufox")
    print(f"Push to webchat2api enabled: {settings.webchat2api_enabled} (URL: {settings.webchat2api_base_url})")

    total = 50
    success = 0
    fail = 0
    results = []

    print(f"\n=== Starting custom registration of {total} accounts ===")
    
    # Open log file to append output
    log_file_path = "logs/batch_register_50.log"
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    
    for i in range(1, total + 1):
        print(f"\n[{i}/{total}] Starting registration...")
        log_line = f"\n=== Account {i}/{total} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===\n"
        with open(log_file_path, "a", encoding="utf-8") as lf:
            lf.write(log_line)
            
        def log_to_both(msg):
            print(f"  [{i}] {msg}")
            try:
                with open(log_file_path, "a", encoding="utf-8") as lf:
                    lf.write(f"{time.strftime('%H:%M:%S')} - {msg}\n")
            except Exception:
                pass

        engine = RegistrationEngineV2(
            email_service=email_service,
            proxy_url=proxy_url,
            browser_mode="camoufox",
            callback_logger=log_to_both,
            max_retries=3,
        )
        
        try:
            result = engine.run()
        except Exception as e:
            import traceback
            traceback.print_exc()
            result = type("R", (), {"success": False, "email": "", "account_id": "", "error_message": str(e)})()

        saved = engine.save_to_database(result) if result.success else False
        
        # Verify manual push just in case background push had issues
        push_status = "Skipped"
        if result.success and result.access_token:
            from src.core.upload.webchat2api_upload import push_registration_to_webchat2api
            ok, msg = push_registration_to_webchat2api(result, settings.webchat2api_base_url, settings.webchat2api_api_token.get_secret_value())
            push_status = f"{ok} ({msg})"
            log_to_both(f"Manual Push Status: {push_status}")

        r = {
            "index": i,
            "success": result.success,
            "email": result.email,
            "account_id": result.account_id,
            "error_message": getattr(result, "error_message", "unknown error"),
            "db_saved": saved,
            "push_status": push_status
        }
        results.append(r)

        if r["success"]:
            success += 1
            log_to_both(f"SUCCESS: {r['email']} (ID: {r['account_id']})")
        else:
            fail += 1
            log_to_both(f"FAIL: {r['error_message']}")

        # Cooldown sleep to avoid speed block
        time.sleep(10)

    print(f"\nBatch complete: {success}/{total} succeeded")
    print(f"Detailed logs saved to {log_file_path}")

if __name__ == "__main__":
    main()
