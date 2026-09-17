import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from microteleop_sdk.control.crypto import SigningKey
from microteleop_sdk.session import RobotSession


def session(tmp_path):
    robot = SigningKey.generate(str(tmp_path / "robot.pem"))
    platform = SigningKey.generate(str(tmp_path / "platform.pem"))
    trusted = tmp_path / "trust.json"
    trusted.write_text(json.dumps({platform.key_id: platform.public_key}))
    stops = []
    value = RobotSession(
        platform_url="https://platform.test",
        robot_id="g1d",
        site_id="munich",
        private_key_file=tmp_path / "robot.pem",
        platform_keys_file=trusted,
        safe_state=lambda: stops.append(True),
        is_safe=lambda: True,
        max_permit_seconds=15,
        clock_uncertainty_seconds=0.05,
        command_timeout_seconds=0.5,
    )
    assert value.client.key.key_id == robot.key_id
    now = datetime.now(timezone.utc)
    token = platform.sign(
        {
            "schema_version": 1,
            "permit_id": "permit",
            "issuer": "https://platform.test",
            "audience": "robot-control",
            "site_id": "munich",
            "robot_id": "g1d",
            "sdk_instance_id": value.client.instance,
            "session_id": "session",
            "operator_id": "user",
            "participant_identity": "operator",
            "ownership_version": 1,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=10)).isoformat(),
        },
        "control-permit",
    )
    value.client.guard.accept_permit(token)
    return value, stops, token


def packet(sequence=1, sender="operator", **changes):
    control = {
        "session_id": "session",
        "ownership_version": 1,
        "sequence": sequence,
        "sent_at_ms": time.time() * 1000,
    }
    control.update(changes)
    return SimpleNamespace(
        topic="vr-controller-data",
        participant=SimpleNamespace(identity=sender),
        data=json.dumps({"control": control, "command": {"g1d": {"left": "fixture"}}}).encode(),
    )


def test_sender_replay_and_unsigned_packets_never_reach_controller(tmp_path):
    sdk, _, _ = session(tmp_path)
    assert not sdk.receive(packet(sender="viewer"))
    assert sdk.latest is None
    assert sdk.receive(packet())
    first = sdk.latest
    assert sdk.current(first)
    assert not sdk.receive(packet())
    assert not sdk.receive(SimpleNamespace(topic="vr-controller-data", data=b"{}"))
    assert sdk.latest is first


def test_stop_clears_mailbox_and_invalidates_inflight_work(tmp_path):
    sdk, stops, token = session(tmp_path)
    assert sdk.receive(packet())
    inflight = sdk.latest
    sdk.client.guard.stop()
    assert stops and sdk.latest is None and not sdk.current(inflight)
    assert not sdk.receive(packet(2))
    import pytest

    from microteleop_sdk.control.crypto import AuthenticationError

    with pytest.raises(AuthenticationError):
        sdk.client.guard.accept_permit(token)


def test_watchdog_stops_without_new_packets(tmp_path):
    sdk, stops, _ = session(tmp_path)
    assert sdk.receive(packet())
    sdk.client.guard.tick(monotonic_now=sdk.client.guard.command_deadline + 1)
    assert sdk.latest is None and len(stops) == 2


def test_stale_timestamp_cannot_refresh_latest_input(tmp_path):
    sdk, _, _ = session(tmp_path)
    assert not sdk.receive(packet(sent_at_ms=(time.time() - 2) * 1000))
    assert sdk.latest is None
