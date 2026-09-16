import asyncio
import logging

import numpy as np
import requests

from microteleop_sdk.camera.camera_streamer import CameraStreamer
from microteleop_sdk.config import DEFAULT_BACKEND_URL, MicroTeleopConfig
from microteleop_sdk.inputs.base import ArmGoal
from microteleop_sdk.inputs.vr_controller import VRController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s.%(funcName)s: %(message)s")


class MicroTeleopAPI:
    def __init__(self, api_key: str, *, backend_url: str = DEFAULT_BACKEND_URL):
        self.config = MicroTeleopConfig(backend_url=backend_url.rstrip("/"))
        self.api_key = api_key
        self.backend_url = self.config.backend_url

    async def authenticate(self, participant_identity: str):
        """
        Authenticate robot and get LiveKit session.
        """
        url = f"{self.backend_url}/api/sdk/auth"

        payload = {
            "api_key": self.api_key,
            "participant_identity": participant_identity,
        }

        try:
            response = requests.post(url, json=payload, timeout=10, verify=False)
            return response.json()

        except requests.exceptions.RequestException as e:
            print(f"❌ Authentication failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_detail = e.response.text
                    print(f"Error details: {error_detail}")
                except Exception as e:
                    print(f"Error details: {e}")
            raise

    async def connect_vr_controller(self):
        """
        Connect to the VR controllers.
        Run this before calling get_controller_goal.
        """
        livekit_data = await self.authenticate(self.config.controller_participant)
        self.vr_controller = VRController()
        await self.vr_controller.start(
            livekit_data["room_name"],
            self.config.controller_participant,
            livekit_data["token"],
            livekit_data["livekit_url"],
        )

    async def connect_camera_streamer(self, height: int, width: int):
        """
        Connect the camera streamer to the VR Headset.
        Run this before calling send_stereo_frame or send_single_frame.
        """
        livekit_data = await self.authenticate(self.config.camera_participant)
        self.camera_streamer = CameraStreamer(height, width)
        await self.camera_streamer.start(
            livekit_data["room_name"],
            self.config.camera_participant,
            livekit_data["token"],
            livekit_data["livekit_url"],
        )

    async def disconnect_vr_controller(self):
        """
        Disconnect from the VR controllers.
        Run this after you are finished with the VR controllers.
        """
        if not self.vr_controller:
            return
        await self.vr_controller.stop()

    async def disconnect_camera_streamer(self):
        """
        Disconnect from the camera streamer.
        Run this after you are finished with the camera streamer.
        """
        if not self.camera_streamer:
            return
        await self.camera_streamer.stop()

    async def send_stereo_frame(self, left_frame: np.ndarray, right_frame: np.ndarray):
        """Send the left and right frames of a stereo camera to the camera streamer.
           The frames should be horizontally concatenated.

        Args:
            frame (np.ndarray): The horizontally concatenated stereo frame, shape (height, 2*width, 3).

        Raises:
            ValueError: Camera streamer not connected
        """
        frame = np.concatenate([left_frame, right_frame], axis=1)
        if not self.camera_streamer:
            raise ValueError("Camera streamer not connected")
        await self.camera_streamer.send_stereo_frame(frame)
        await asyncio.sleep(0.001)

    async def send_single_frame(self, frame: np.ndarray):
        """Send the single frame from a mono camera to the camera streamer.

        Args:
            frame (np.ndarray): The single frame, shape (height, width, 3).

        Raises:
            ValueError: Camera streamer not connected
        """
        if not self.camera_streamer:
            raise ValueError("Camera streamer not connected")
        await self.camera_streamer.send_single_frame(frame)

    async def get_controller_goal(self, arm: str) -> ArmGoal:
        """Get the VR Controller information.

        Args:
            arm (str): The arm to get the goal for.

        Returns:
            ArmGoal: Contains the following fields:
                - arm: left or right.
                - relative_transform: The relative transform of the VR Controller relative to the reference frame.
                - gripper_closed: False if trigger button is pressed, otherwise True.
                - reset_to_init: True if the the X, or A button is pressed, otherwise False.
                - reset_reference: True if the Grip button is activated, otherwise False.
                                   Sets the VR reference frame to the current frame.
        """
        if not self.vr_controller:
            raise ValueError("VR controller not connected")
        await asyncio.sleep(0.001)
        return self.vr_controller.get_controller_goal(arm)
