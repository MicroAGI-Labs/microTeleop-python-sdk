# microTeleop SDK - Python

## Overview

The microTeleop SDK allows for real-time robot teleoperation right from the web-browser of your VR Headset.


### Core functionalities
- (egocentric) stereo and mono camera streaming
- operator controls for recording data
- direct integration with LeRobot for data recording 
- **Easy Setup**: Connect your robot in minutes - configure an API key on a compatible microTeleop backend (see Backend compatibility below), plug it into the Python SDK, and access your robot directly from the VR headset’s native browser.


## Supported data streams:
- **VR Controller Input**: End-effector pose, button signals from the controlle, trigger, grip

## Supported data transfer protocols:
- WebRTC (powered by livekit)

## Supported VR Headsets
- Meta Quest 3
- Meta Quest 3s

## Configure your own operator workflows:
- Create your own mappings for VR-Controller buttons
- Configure vide stream and layouts


## Quickstart

### Conda Installation

```bash
git clone git@github.com:MicroAGI-Labs/microTeleop-python-sdk.git
cd microTeleop-python-sdk
conda create -n microteleop python=3.10
conda activate microteleop
pip install -e .
```

### UV Installation

```bash
git clone git@github.com:MicroAGI-Labs/microTeleop-python-sdk.git
cd microTeleop-python-sdk
uv sync
```

### Configure the backend

The SDK defaults to Atlas Core at [https://atlas-core-api-f2qyzkorqq-oa.a.run.app](https://atlas-core-api-f2qyzkorqq-oa.a.run.app). Set your robot API key for the examples:

```bash
export MICROTELEOP_API_KEY="your-robot-api-key"
```

To override the default, pass `backend_url` when constructing `MicroTeleopAPI`, or set `MICROTELEOP_BACKEND_URL` for the examples.

When upgrading an existing integration, reinstall the package and update imports to `microteleop_sdk` and the API class to `MicroTeleopAPI`.

### Backend compatibility

This SDK still uses API-key authentication through `POST /api/sdk/auth`. Current Atlas Core explicitly does not expose that route: its [robot SDK contract](https://github.com/MicroAGI-Labs/atlas-core/blob/main/specs/teleop/SDK_PROTOCOL.md) requires signed challenge/proof authentication and control permits. The branding and default URL update does not implement that protocol migration. The example below requires a backend that supports the existing SDK protocol.

### Minimal Example

```python
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
```
### On the VR Headset

Atlas Core hosts the Quest interface at `/quest`. The example requires a compatible SDK backend as described above; setting the Atlas URL alone does not enable robot control.

#### Controller Mapping

- **Grip Button**: Sets reference frame when first pressed, enables position control when held
- **Trigger Button**: Controls gripper (pressed = open, released = closed)
- **Reset Button (X/A)**: Trigger to reset arm to initial position
- **Position Tracking**: Controller movement translates to arm movement when grip is held
