import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from test_session import health, session

from microteleop_sdk.control.contracts import CompatibilityHello
from microteleop_sdk.control.crypto import AuthenticationError


def test_hello_is_required_strict_and_release_bound():
    with pytest.raises(ValidationError):
        CompatibilityHello.model_validate({})
    from test_session import HELLO

    for change in (
        {"sdk_version": "0.2.1"},
        {"sdk_sha": "main"},
        {"extra": True},
        {"protocol_version": True},
        {"capabilities": ["upload-v2"]},
    ):
        with pytest.raises(ValidationError):
            CompatibilityHello.model_validate_json(json.dumps(HELLO | change))


@pytest.mark.parametrize(
    "change",
    [
        {"accepted": False, "reason": "unsupported-sdk"},
        {"contract_digest": "0" * 64},
        {"protocol_version": 1},
        {"extra": True},
    ],
)
def test_rejected_compatibility_never_signs_proof(tmp_path, change):
    sdk, _, _ = session(tmp_path)
    calls = []

    def post(path, body):
        calls.append(path)
        return {
            "challenge_id": "test",
            "challenge": "untrusted",
            "compatibility": {
                "protocol_version": 2,
                "accepted": True,
                "reason": None,
                "contract_digest": sdk.client.contract_digest,
            }
            | change,
        }

    sdk.client._post = post
    with pytest.raises((AuthenticationError, ValidationError)):
        sdk.client.connect()
    assert calls == ["challenges"]
    assert sdk.client.guard.permit is None


@pytest.mark.parametrize(
    "change",
    [
        {"sdk_instance_id": "wrong"},
        {"robot_id": "wrong"},
        {"stop": "false"},
        {"profile_sha256": "0" * 64},
        {"health_sequence": True},
        {"required_views_fresh": False},
        {"health_expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
    ],
)
def test_bad_health_fences_authority(tmp_path, change):
    sdk, _, token = session(tmp_path)
    with pytest.raises((AuthenticationError, ValidationError)):
        sdk.client.process_state(health(sdk, token) | change)
    assert sdk.client.guard.permit is None


def test_view_expiry_cannot_be_extended_by_repeated_poll(tmp_path, monkeypatch):
    sdk, _, token = session(tmp_path)
    state = health(sdk, token)
    sdk.client.process_state(state)
    deadline = sdk.client.health_deadline
    sdk.client.process_state(state)
    assert sdk.client.health_deadline <= deadline + 0.001
    monkeypatch.setattr("microteleop_sdk.control.robot.time.monotonic", lambda: deadline + 1)
    assert not sdk.client.tick()
    assert sdk.client.guard.permit is None


def test_signed_challenge_binds_exact_hello_and_returns_current_health(tmp_path):
    from microteleop_sdk.control.crypto import SigningKey

    sdk, _, token = session(tmp_path)
    platform = SigningKey.load(str(tmp_path / "platform.pem"))
    calls = []

    def post(path, body):
        calls.append((path, body))
        if path == "challenges":
            now = datetime.now(timezone.utc)
            assert body["compatibility"] == sdk.client.compatibility.model_dump(mode="json")
            return {
                "challenge_id": "challenge",
                "challenge": platform.sign(
                    body
                    | {
                        "challenge_id": "challenge",
                        "issuer": "https://platform.test",
                        "issued_at": now.isoformat(),
                        "expires_at": (now + timedelta(seconds=10)).isoformat(),
                    },
                    "robot-challenge",
                ),
                "compatibility": {
                    "protocol_version": 2,
                    "accepted": True,
                    "reason": None,
                    "contract_digest": sdk.client.contract_digest,
                },
            }
        from microteleop_sdk.control.crypto import TrustStore

        proof = TrustStore({sdk.client.key.key_id: sdk.client.key.public_key}).verify(
            body["proof"], "robot-proof"
        )
        sdk.client.trust.verify(proof["challenge"], "robot-challenge")
        return health(sdk, token, operation="connect")

    sdk.client._post = post
    result = sdk.client.connect()
    sdk.client.process_state(result)
    assert sdk.client.tick()
    assert [call[0] for call in calls] == ["challenges", "proofs"]
