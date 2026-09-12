# microTeleop SDK - Python

## Overview

The Tactile Teleop SDK allows for real-time robot teleoperation right from the web-browser of your VR Headset.


### Core functionalities
- (egocentric) stereo and mono camera streaming
- operator controls for recording data
- direct integration with LeRobot for data recording 
- **Easy Setup**: Connect your robot in minutes - just generate an API key at [teleop.tactilerobotics.ai](https://teleop.tactilerobotics.ai), plug it into the Python SDK, and access your robot directly from the VR headset’s native browser.


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
git clone git@github.com:TactileRoboticsAI/tactile-teleop-python-sdk.git
cd tactile-teleop-python-sdk
conda create -n tactile python=3.10
conda activate tactile
pip install -e .
```

### UV Installation

```bash
git clone git@github.com:TactileRoboticsAI/tactile-teleop-python-sdk.git
cd tactile-teleop-python-sdk
uv sync
```

### Create API key

Visit [teleop.tactilerobotics.ai](https://teleop.tactilerobotics.ai), register and create a new robot. Save the automatically generated API key.

### Key Concept
The robot API key authorizes you (anyone) to control your robot, once it is ready to be operated. 
The robot API key gets generated from the webserver (supabase backend) and it associated to a specific user.
A user can only operate a robot that has the same robot API key as associated to its account. 

### Minimal Example

```python
import asyncio
import os

import numpy as np

from tactile_teleop_sdk import TactileAPI
from tactile_teleop_sdk.utils.visualizer import TransformVisualizer

CAMERA_WIDTH = 580
CAMERA_HEIGHT = 480
API_KEY = ""  # NOTE: Replace with your robot API key from https://teleop.tactilerobotics.ai


async def main():
    # Initialize the API
    api = TactileAPI(api_key=API_KEY)

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

Go to the Browser of your VR headset and visit [teleop.tactilerobotics.ai](https://teleop.tactilerobotics.ai). Login, and press start VR Control on the robot.

#### Controller Mapping

- **Grip Button**: Sets reference frame when first pressed, enables position control when held
- **Trigger Button**: Controls gripper (pressed = open, released = closed)
- **Reset Button (X/A)**: Trigger to reset arm to initial position
- **Position Tracking**: Controller movement translates to arm movement when grip is held
