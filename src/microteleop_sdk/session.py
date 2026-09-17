"""Signed v1 robot session for development integrations.

The SDK owns transport/authority; callers own robot control and safe-state behavior.
This does not implement the v2 release/recording/video-health contract.
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


class RobotSession:
    """A bounded latest-command mailbox behind signed permits and a watchdog.

    ``safe_state`` must promptly inhibit the robot, without blocking network I/O.
    ``is_safe`` confirms completion before acknowledging a new platform epoch.
    Use ``current(sample)`` again after slow work and before submitting output.
    """

    def __init__(self, *, safe_state, is_safe, **identity):
        self.latest = None
        self.room = None
        self._tasks = []
        self._video = None
        self._stopping = False

        def stop():
            self.latest = None
            safe_state()

        self.client = RobotControlClient(safe_state=stop, is_safe=is_safe, **identity)

    def receive(self, packet):
        """Admit a LiveKit data packet; invalid senders never reach the mailbox."""
        if packet.topic != "vr-controller-data" or len(packet.data) > 65536:
            return False
        try:
            payload = json.loads(packet.data)
            control, command = payload["control"], payload["command"]
            if not isinstance(command, dict):
                return False
            self.client.guard.authorize(
                packet.participant.identity,
                control["session_id"],
                control["ownership_version"],
                control["sequence"],
                control["sent_at_ms"],
            )
            self.latest = ReceivedCommand(
                control["sequence"],
                control["ownership_version"],
                time.monotonic(),
                command,
            )
            return True
        except (AuthenticationError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def current(self, sample):
        guard = self.client.guard
        return (
            sample is not None
            and guard.tick()
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
            await asyncio.sleep(0.1)

    async def _watchdog(self):
        while not self._stopping:
            self.client.guard.tick()
            await asyncio.sleep(min(0.025, self.client.guard.command_timeout / 4))

    async def publish_rgb(self, rgb, *, name="g1d-ego-mono"):
        """Publish a true mono RGB view. Rendering/encoding is outside the control loop."""
        import numpy as np
        from livekit import rtc

        if self.room is None:
            raise RuntimeError("session is not connected")
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("expected uint8 HxWx3 RGB")
        height, width = rgb.shape[:2]
        if self._video is None:
            source = rtc.VideoSource(width, height)
            track = rtc.LocalVideoTrack.create_video_track(name, source)
            await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions())
            self._video = source, width, height, name
        source, expected_width, expected_height, expected_name = self._video
        if (width, height, name) != (expected_width, expected_height, expected_name):
            raise ValueError("camera geometry/name changed during session")
        source.capture_frame(
            rtc.VideoFrame(
                width, height, rtc.VideoBufferType.RGB24, np.ascontiguousarray(rgb).tobytes()
            )
        )

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
