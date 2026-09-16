import asyncio
import os

import numpy as np

from microteleop_sdk import MicroTeleopAPI
from microteleop_sdk.config import DEFAULT_BACKEND_URL
from microteleop_sdk.utils.visualizer import TransformVisualizer

CAMERA_WIDTH = 580
CAMERA_HEIGHT = 480
API_KEY = os.environ["MICROTELEOP_API_KEY"]
BACKEND_URL = os.environ.get("MICROTELEOP_BACKEND_URL", DEFAULT_BACKEND_URL)


async def main():
    # Initialize the API
    api = MicroTeleopAPI(api_key=API_KEY, backend_url=BACKEND_URL)

    # Initialize visualizer (will open browser automatically)
    visualizer = TransformVisualizer()

    # Connect VR controllers
    await api.connect_vr_controller()

    # Connect camera streamer
    await api.connect_camera_streamer(CAMERA_HEIGHT, CAMERA_WIDTH)

    try:
        while True:
            # Get VR controller input
            left_goal = await api.get_controller_goal("left")
            right_goal = await api.get_controller_goal("right")

            # Visualize VR controller relative transforms
            if visualizer:
                visualizer.update_visualization(
                    left_transform=left_goal.relative_transform,
                    right_transform=right_goal.relative_transform,
                )

            # Send camera frame (example with dummy data)
            dummy_frame = np.random.randint(0, 255, (CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
            await api.send_single_frame(dummy_frame)

            await asyncio.sleep(0.01)

    finally:
        await api.disconnect_vr_controller()
        await api.disconnect_camera_streamer()

if __name__ == "__main__":
    asyncio.run(main())
