import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from microteleop_sdk import RobotSession
from microteleop_sdk.control.crypto import SigningKey

HELLO = {
    "protocol_version": 2,
    "sdk_version": "0.3.0",
    "sdk_sha": "a" * 40,
    "capabilities": ["signed-control-v1", "view-health-v2", "camera-source-timestamp-v1"],
    "profile_sha256": "b" * 64,
}


def health(sdk, token, **changes):
    now = datetime.now(timezone.utc)
    return (
        dict(
            robot_id="g1d",
            site_id="munich",
            sdk_instance_id=sdk.client.instance,
            room_name="room",
            session_id="session",
            participant_identity="operator",
            ownership_version=1,
            permit=token,
            expires_at=(now + timedelta(seconds=10)).isoformat(),
            server_time=now.isoformat(),
            permit_seconds=10,
            profile_sha256=HELLO["profile_sha256"],
            control_paused=False,
            pause_reason=None,
            required_views_fresh=True,
            optional_view_warnings=[],
            health_sequence=1,
            health_expires_at=(now + timedelta(seconds=1)).isoformat(),
            transport="livekit",
            transport_status="accepted",
            video={"provider": "livekit"},
            operation="poll",
            node_id="robot",
            stop=False,
            ready=True,
        )
        | changes
    )


def session(tmp_path):
    robot = SigningKey.generate(str(tmp_path / "robot.pem"))
    platform = SigningKey.generate(str(tmp_path / "platform.pem"))
    trusted = tmp_path / "trust.json"
    trusted.write_text(json.dumps({platform.key_id: platform.public_key}))
    stops = []
    value = RobotSession(
        compatibility=HELLO,
        contract_digest="c" * 64,
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
    value.client.process_state(health(value, token))
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


def test_platform_pause_clears_input_and_resume_preserves_ownership(tmp_path):
    sdk, stops, token = session(tmp_path)
    assert sdk.receive(packet())
    state = health(sdk, token, control_paused=True)
    sdk.client.process_state(state)
    assert sdk.latest is None and not sdk.receive(packet(2))
    assert not sdk.client.guard.tick(monotonic_now=sdk.client.guard.command_deadline + 1)
    assert sdk.client.guard.permit is not None
    sdk.client.process_state(dict(state, control_paused=False))
    assert sdk.receive(packet(3))
    assert sdk.current(sdk.latest)
    assert sdk.client.guard.fenced_version == 0 and len(stops) >= 2


def test_platform_resume_waits_for_measured_hold(tmp_path):
    sdk, _, token = session(tmp_path)
    state = health(sdk, token, control_paused=True)
    sdk.client.process_state(state)
    sdk.client.is_safe = lambda: False
    sdk.client.process_state(dict(state, control_paused=False))
    assert not sdk.receive(packet())
    sdk.client.is_safe = lambda: True
    sdk.client.process_state(dict(state, control_paused=False))
    assert sdk.receive(packet(2))


def test_publish_retains_capture_clock_and_rejects_repeat(tmp_path, monkeypatch):
    import asyncio

    import numpy as np
    from livekit import rtc

    sdk, _, _ = session(tmp_path)
    captured = []

    class Source:
        def __init__(self, *_):
            pass

        def capture_frame(self, frame, *, timestamp_us):
            captured.append(timestamp_us)

    async def publish(*_):
        pass

    monkeypatch.setattr(
        sdk, "room", SimpleNamespace(local_participant=SimpleNamespace(publish_track=publish))
    )
    monkeypatch.setattr(rtc, "VideoSource", Source)
    monkeypatch.setattr(rtc.LocalVideoTrack, "create_video_track", lambda *_: object())
    stamp = time.monotonic_ns() - 10_000_000
    rgb = np.zeros((8, 8, 3), np.uint8)
    asyncio.run(sdk.publish_rgb(rgb, captured_at_ns=stamp))
    assert captured == [stamp // 1000]
    import pytest

    with pytest.raises(ValueError, match="capture"):
        asyncio.run(sdk.publish_rgb(rgb, captured_at_ns=stamp))
    with pytest.raises(ValueError, match="capture"):
        asyncio.run(sdk.publish_rgb(rgb, captured_at_ns=time.monotonic_ns() - 200_000_000))
    assert len(captured) == 1


def test_resume_cannot_reuse_inflight_or_queued_pre_pause_input(tmp_path):
    sdk, _, token = session(tmp_path)
    assert sdk.receive(packet())
    inflight = sdk.latest
    queued = packet(2)
    state = health(sdk, token, control_paused=True)
    sdk.client.process_state(state)
    sdk.client.process_state(dict(state, control_paused=False))
    assert not sdk.current(inflight)
    assert not sdk.receive(queued)
    assert sdk.receive(packet(3))


def test_pause_still_expires_authority_and_preserves_stop_fence(tmp_path):
    sdk, _, token = session(tmp_path)
    state = health(sdk, token, control_paused=True)
    sdk.client.process_state(state)
    sdk.client.guard.tick(monotonic_now=sdk.client.guard.deadline + 1)
    assert sdk.client.guard.permit is None
    sdk.client.process_state(dict(state, control_paused=False))
    assert not sdk.receive(packet()) and sdk.client.guard.fenced_version == 1
