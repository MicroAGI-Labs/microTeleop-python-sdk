import logging
from typing import Optional

import numpy as np
from livekit import rtc

logger = logging.getLogger(__name__)


class CameraStreamer:
    def __init__(
        self,
        height: int,
        width: int,
    ):
        self.width = width * 2
        self.height = height
        self.source = rtc.VideoSource(self.width, self.height)

        self.track = rtc.LocalVideoTrack.create_video_track("robot0-birds-eye", self.source)
        self.room: Optional[rtc.Room] = None

        self.options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            simulcast=False,
            video_encoding=rtc.VideoEncoding(
                max_framerate=30,
                max_bitrate=3_000_000,
            ),
            video_codec=rtc.VideoCodec.H264,
        )

    async def send_single_frame(self, frame: np.ndarray):
        concatenated_frame = np.concatenate([frame, frame], axis=1)

        frame_bytes = concatenated_frame.tobytes()
        video_frame = rtc.VideoFrame(
            self.width,
            self.height,
            rtc.VideoBufferType.RGB24,
            frame_bytes,
        )
        self.source.capture_frame(video_frame)

    async def send_stereo_frame(self, frame: np.ndarray):
        frame_bytes = frame.tobytes()
        video_frame = rtc.VideoFrame(
            self.width,
            self.height,
            rtc.VideoBufferType.RGB24,
            frame_bytes,
        )
        self.source.capture_frame(video_frame)

    async def _publish_track(self, room: rtc.Room):
        try:
            await room.local_participant.publish_track(self.track, self.options)
            logger.info("(CameraStreamer) Published video track successfully")
        except Exception as e:
            logger.error(f"Failed to publish video track: {e}", exc_info=True)
            raise

    async def start(self, room_name: str, participant_name: str, token: str, livekit_url: str):
        self.room = rtc.Room()

        @self.room.on("participant_connected")
        def on_participant_connected(participant):
            print(f"🟢 Participant connected: {participant.identity}")

        @self.room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            print(f"🔴 Participant disconnected: {participant.identity}")

        @self.room.on("track_published")
        def on_track_published(publication, participant):
            print(f"📹 Track published by {participant.identity}: {publication.kind}")

        try:
            await self.room.connect(livekit_url, token)
            logger.info(f"✅ Connected to LiveKit room {room_name} as {participant_name}")
            logger.info(f"🔍 Room connection state: {self.room.connection_state}")
            logger.info(f"🔍 Local participant: {self.room.local_participant.identity}")
            logger.info(f"📊 Room participants: {len(self.room.remote_participants)}")
            for participant in self.room.remote_participants.values():
                logger.info(f"  - {participant.identity} ({participant.sid})")

            await self._publish_track(self.room)

        except KeyboardInterrupt:
            logger.info("(CameraStreamer) ⌨️  KeyboardInterrupt, shutting down")
        except Exception as e:
            logger.error(f"💥 Error in camera publisher: {e}", exc_info=True)

    async def stop(self, timeout: float = 5.0):
        logger.info("(CameraStreamer) Stopping camera streamer task...")

        if hasattr(self, "room") and self.room:
            await self.room.disconnect()
        logger.info("(CameraStreamer) 🏁 Camera publisher shutdown complete")
