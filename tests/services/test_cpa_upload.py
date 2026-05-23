from datetime import datetime, timezone

from src.core.upload.cpa_upload import generate_token_json


class DummyAccount:
    email = "user@example.test"
    access_token = "access-token"
    refresh_token = "refresh-token"
    id_token = "id-token"
    session_token = "session-token"
    account_id = "account-123"
    workspace_id = "workspace-123"
    expires_at = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    last_refresh = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    subscription_type = None
    extra_data = {
        "raw_session": {
            "account": {
                "id": "account-123",
                "plan_type": "plus",
                "chatgpt_plan_type": "plus",
            }
        },
        "account": {
            "plan_type": "plus",
        },
    }


def test_generate_token_json_includes_session_token() -> None:
    token_json = generate_token_json(DummyAccount())

    assert token_json["session_token"] == "session-token"


def test_generate_token_json_includes_plan_type_when_available() -> None:
    token_json = generate_token_json(DummyAccount())

    assert token_json["plan_type"] == "plus"
    assert token_json["chatgpt_plan_type"] == "plus"


def test_generate_token_json_reads_camel_case_plan_type() -> None:
    class CamelCasePlanAccount(DummyAccount):
        subscription_type = None
        extra_data = {
            "raw_session": {
                "account": {
                    "id": "account-123",
                    "planType": "plus",
                }
            }
        }

    token_json = generate_token_json(CamelCasePlanAccount())

    assert token_json["plan_type"] == "plus"
    assert token_json["chatgpt_plan_type"] == "plus"
