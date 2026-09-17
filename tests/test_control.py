import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from microteleop_sdk.control.crypto import (
    AuthenticationError,
    SigningKey,
    TrustStore,
    public_key_text,
)
from microteleop_sdk.control.permit import ControlPermit
from microteleop_sdk.control.receiver import ControlReceiver


def permit(**changes):
    now = datetime.now(timezone.utc)
    values = {
        "permit_id": "permit-1",
        "issuer": "https://platform.test",
        "audience": "robot-control",
        "site_id": "site",
        "robot_id": "robot",
        "sdk_instance_id": "boot",
        "session_id": "session",
        "operator_id": "alice",
        "participant_identity": "operator-alice",
        "ownership_version": 1,
        "issued_at": now,
        "expires_at": now + timedelta(seconds=10),
    }
    values.update(changes)
    return ControlPermit(**values)


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    signer = SigningKey(private)
    return signer, TrustStore({signer.key_id: public_key_text(private.public_key())})


def test_signature_tamper_unknown_key_and_wrong_purpose(keys):
    signer, trust = keys
    signed = signer.sign(permit().model_dump(mode="json"), "control-permit")
    assert trust.verify(signed, "control-permit")["robot_id"] == "robot"
    with pytest.raises(AuthenticationError):
        trust.verify(signed, "robot-challenge")
    with pytest.raises(AuthenticationError):
        TrustStore({}).verify(signed, "control-permit")
    payload = json.dumps(permit(robot_id="other").model_dump(mode="json")).encode()
    parts = signed.split(".")
    from jwt.utils import base64url_encode

    parts[1] = base64url_encode(payload).decode()
    with pytest.raises(AuthenticationError):
        trust.verify(".".join(parts), "control-permit")


def receiver(keys, stopped):
    return ControlReceiver(
        keys[1],
        issuer="https://platform.test",
        site_id="site",
        robot_id="robot",
        sdk_instance_id="boot",
        safe_state=lambda: stopped.append(True),
        max_permit_seconds=15,
        clock_uncertainty_seconds=0.1,
        command_timeout_seconds=0.5,
    )


def test_receiver_sender_replay_expiry_and_safe_state(keys):
    stopped = []
    guard = receiver(keys, stopped)
    p = permit()
    guard.accept_permit(keys[0].sign(p.model_dump(mode="json"), "control-permit"))
    guard.authorize("operator-alice", "session", 1, 1, p.issued_at.timestamp() * 1000)
    with pytest.raises(AuthenticationError):
        guard.authorize("mallory", "session", 1, 2, p.issued_at.timestamp() * 1000)
    with pytest.raises(AuthenticationError):
        guard.authorize("operator-alice", "session", 1, 1, p.issued_at.timestamp() * 1000)
    guard.stop()
    assert stopped == [True]
    with pytest.raises(AuthenticationError):
        guard.accept_permit(keys[0].sign(p.model_dump(mode="json"), "control-permit"))


@pytest.mark.parametrize(
    "changes",
    [
        {"robot_id": "other"},
        {"sdk_instance_id": "old-boot"},
        {"issuer": "https://evil.test"},
        {"audience": "room-access"},
        {
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
            "issued_at": datetime.now(timezone.utc) - timedelta(seconds=5),
        },
        {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)},
    ],
)
def test_receiver_rejects_wrong_context_or_lifetime(keys, changes):
    guard = receiver(keys, [])
    with pytest.raises(AuthenticationError):
        guard.accept_permit(
            keys[0].sign(permit(**changes).model_dump(mode="json"), "control-permit")
        )


def test_watchdog_does_not_require_an_incoming_packet(keys):
    stopped = []
    guard = receiver(keys, stopped)
    guard.accept_permit(keys[0].sign(permit().model_dump(mode="json"), "control-permit"))
    guard.tick(monotonic_now=guard.deadline + 1)
    assert stopped == [True]


def test_future_ownership_cannot_replace_active_controller(keys):
    guard = receiver(keys, [])
    guard.accept_permit(keys[0].sign(permit().model_dump(mode="json"), "control-permit"))
    with pytest.raises(AuthenticationError):
        guard.accept_permit(
            keys[0].sign(
                permit(ownership_version=2, session_id="new").model_dump(mode="json"),
                "control-permit",
            )
        )


def test_command_age_and_renewal_after_local_expiry(keys):
    import time

    guard = receiver(keys, [])
    p = permit()
    guard.accept_permit(keys[0].sign(p.model_dump(mode="json"), "control-permit"))
    with pytest.raises(AuthenticationError):
        guard.authorize("operator-alice", "session", 1, 1, (time.time() - 2) * 1000)
    with pytest.raises(AuthenticationError):
        guard.authorize("operator-alice", "session", 1, 1, (time.time() + 2) * 1000)
    guard.deadline = time.monotonic() - 1
    with pytest.raises(AuthenticationError):
        guard.accept_permit(keys[0].sign(permit().model_dump(mode="json"), "control-permit"))


def test_wrong_algorithm_and_external_key_headers_are_rejected(keys):
    import jwt

    signer, trust = keys
    token = jwt.api_jws.PyJWS().encode(
        b"{}",
        "attacker",
        algorithm="HS256",
        headers={"kid": signer.key_id, "typ": "control-permit"},
    )
    with pytest.raises(AuthenticationError):
        trust.verify(token, "control-permit")
    token = jwt.api_jws.PyJWS().encode(
        b"{}",
        signer._key,
        algorithm="EdDSA",
        headers={
            "kid": signer.key_id,
            "typ": "control-permit",
            "jku": "https://attacker.invalid/keys",
        },
    )
    with pytest.raises(AuthenticationError):
        trust.verify(token, "control-permit")


def test_key_generation_is_private_and_never_overwrites(tmp_path):
    import stat

    path = tmp_path / "robot.pem"
    key = SigningKey.generate(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert SigningKey.load(str(path)).key_id == key.key_id
    with pytest.raises(FileExistsError):
        SigningKey.generate(str(path))


def test_wall_clock_rollback_cannot_extend_a_replayed_permit(keys, monkeypatch):
    import time

    guard = receiver(keys, [])
    token = keys[0].sign(permit().model_dump(mode="json"), "control-permit")
    guard.accept_permit(token)
    deadline = guard.deadline
    actual = time.time()
    monkeypatch.setattr("microteleop_sdk.control.receiver.time.time", lambda: actual - 3600)
    guard.accept_permit(token)
    assert guard.deadline <= deadline + 0.01
