"""Signed Atlas robot session.

The SDK owns transport/authority; callers own robot control and safe-state behavior.
Recording and live-device qualification are separate from command admission.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from .control.crypto import AuthenticationError
from .control.robot import RobotControlClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReceivedCommand:
    sequence: int
    ownership_version: int
    received_at: float
    command: dict
    control_revision: int = 0


class RobotSession:
    """A bounded latest-command mailbox behind signed permits and a watchdog.

    ``safe_state`` must promptly inhibit the robot and keep the event loop responsive.
    ``is_safe`` confirms completion before acknowledging a new platform epoch.
    Use ``current(sample)`` again after slow work and before submitting output.
    """

    def __init__(self, *, safe_state, is_safe, command_boundary=None, **identity):
        self.latest = None
        self._command_boundary = command_boundary
        self._boundary = None
        self._control_revision = 0
        self._command_event = asyncio.Event()
        self.room = None
        self._tasks = []
        self._video = None
        self._last_capture_ns = -1
        self._capture_origin_ns = time.time_ns() - time.monotonic_ns()
        self._frame_id = 0
        self._stopping = False

        def stop():
            self._control_revision += 1
            self.latest = None
            safe_state()

        self.client = RobotControlClient(safe_state=stop, is_safe=is_safe, **identity)

    def receive(self, packet):
        """Admit an authorized LiveKit data packet into the latest-command mailbox."""
        if packet.topic != "vr-controller-data" or len(packet.data) > 65536:
            return False
        try:
            payload = json.loads(packet.data)
            control, command = payload["control"], payload["command"]
            if not isinstance(command, dict):
                return False
            if not self.client.tick():
                return False
            self.client.guard.authorize(
                packet.participant.identity,
                control["session_id"],
                control["ownership_version"],
                control["sequence"],
                control["sent_at_ms"],
            )
            boundary = self._command_boundary(command) if self._command_boundary else None
            if boundary != self._boundary:
                self._control_revision += 1
                self._boundary = boundary
            self.latest = ReceivedCommand(
                control["sequence"],
                control["ownership_version"],
                time.monotonic(),
                command,
                self._control_revision,
            )
            self._command_event.set()
            return True
        except (AuthenticationError, ValueError, KeyError, TypeError, AttributeError):
            return False

    async def wait_for_command(self, previous=None, *, timeout=None):
        """Wake one control consumer on admitted input; retain only the newest pose."""
        self._command_event.clear()
        if self.latest is None or self.latest is previous:
            try:
                await asyncio.wait_for(self._command_event.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        return self.latest

    def current(self, sample):
        """Keep fresh in-flight work across pose updates, never across control edges.

        The optional command_boundary callback identifies robot-specific control
        state. Without it, only the latest packet can authorize output.
        """
        guard = self.client.guard
        return (
            sample is not None
            and self.latest is not None
            and (
                sample is self.latest
                or (self._command_boundary is not None
                    and sample.control_revision == self.latest.control_revision)
            )
            and self.client.tick()
            and guard.permit is not None
            and guard.permit.ownership_version == sample.ownership_version
            and time.monotonic() - sample.received_at < guard.command_timeout
        )

    async def start(self):
        from livekit import rtc

        if self.room is not None:
            raise RuntimeError("session already started")
        self._stopping = False
        result = await asyncio.to_thread(self.client.connect, "robot")
        self.client.process_state(result)
        room = self.room = rtc.Room()
        room.on("data_received", self.receive)
        room.on("disconnected", lambda *_: self.client.guard.stop())
        try:
            await room.connect(result["protocol_server_url"], result["token"])
            room.local_participant.register_rpc_method(
                "microteleop.capture-clock.v1", lambda _: json.dumps(self.capture_clock())
            )
            self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._watchdog())]
        except BaseException:
            await self.close()
            raise

    async def _poll(self):
        while not self._stopping:
            try:
                result = await asyncio.to_thread(self.client.exchange, "poll")
                if self._stopping:
                    return
                if self.client.process_state(result):
                    await asyncio.to_thread(self.client.exchange, "safe")
            except Exception:  # noqa: BLE001 -- any poll failure must inhibit control
                self.client.guard.stop()
                log.warning("Control authority unavailable; control inhibited")
            await asyncio.sleep(0.02)

    async def _watchdog(self):
        while not self._stopping:
            self.client.tick()
            await asyncio.sleep(min(0.025, self.client.guard.command_timeout / 4))

    def capture_clock(self):
        """Return the stable source clock used by per-frame metadata."""
        return {
            "clock_id": "sdk-capture-clock-v1",
            "boot_id": self.client.instance,
            "ticks_us": (self._capture_origin_ns + time.monotonic_ns()) // 1000,
        }

    async def publish_rgb(self, rgb, *, captured_at_ns, name="front"):
        """Publish RGB with source capture time and frame ID bound to the encoded frame."""
        import numpy as np
        from livekit import rtc

        if self.room is None:
            raise RuntimeError("session is not connected")
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("expected uint8 HxWx3 RGB")
        if (
            type(captured_at_ns) is not int
            or captured_at_ns <= self._last_capture_ns
            or captured_at_ns > time.monotonic_ns()
        ):
            raise ValueError("future or repeated camera capture")
        height, width = rgb.shape[:2]
        if self._video is None:
            source = rtc.VideoSource(width, height)
            track = rtc.LocalVideoTrack.create_video_track(name, source)
            await self.room.local_participant.publish_track(
                track,
                rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_CAMERA,
                    frame_metadata_features=[
                        rtc.FrameMetadataFeature.FMF_USER_TIMESTAMP,
                        rtc.FrameMetadataFeature.FMF_FRAME_ID,
                    ],
                ),
            )
            self._video = source, width, height, name
        source, expected_width, expected_height, expected_name = self._video
        if (width, height, name) != (expected_width, expected_height, expected_name):
            raise ValueError("camera geometry/name changed during session")
        self._frame_id += 1
        source.capture_frame(
            rtc.VideoFrame(
                width, height, rtc.VideoBufferType.RGB24, np.ascontiguousarray(rgb).tobytes()
            ),
            timestamp_us=captured_at_ns // 1000,
            metadata=rtc.FrameMetadata(
                user_timestamp=(self._capture_origin_ns + captured_at_ns) // 1000,
                frame_id=self._frame_id,
            ),
        )
        self._last_capture_ns = captured_at_ns

    async def close(self):
        self._stopping = True
        self.client.guard.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self.room is not None:
            await self.room.disconnect()
            self.room = None
        self._video = None
        self._last_capture_ns = -1
