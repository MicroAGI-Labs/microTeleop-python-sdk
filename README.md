# microTeleop SDK - Python

## Overview

The microTeleop SDK allows for real-time robot teleoperation right from the web-browser of your VR Headset.


### Core functionalities
- (egocentric) stereo and mono camera streaming
- operator controls for recording data
- direct integration with LeRobot for data recording 
- **Easy Setup**: Connect your robot in minutes - configure an API key on a compatible microTeleop backend (see Backend compatibility below), plug it into the Python SDK, and access your robot directly from the VR headset’s native browser.


## Design philosophy

The SDK should be as minimal as possible: a small public API, few dependencies, and straightforward code. Add features and abstractions only when a concrete use case requires them. Keep backend and application logic outside the SDK.

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

The legacy `MicroTeleopAPI` requires a backend with API-key authentication through `POST /api/sdk/auth`. Atlas Core's [robot SDK contract](https://github.com/MicroAGI-Labs/atlas-core/blob/main/specs/teleop/SDK_PROTOCOL.md) uses signed challenge/proof authentication and control permits through `RobotSession`.

### Signed Atlas development session (0.2)

`microteleop_sdk.session.RobotSession` implements Atlas signed v1 challenge/proof,
issuer/key-pinned permits, participant identity checks, replay rejection and an
independent command watchdog. It requires HTTPS, a registered robot signing key,
a platform trust file, and an operator with control ownership. Robot control and
simulation run in their own packages. Release qualification requires the v2
release, recording and required-view-health acceptance gates.

```python
from microteleop_sdk.session import RobotSession

session = RobotSession(
    platform_url="https://your-atlas-host", robot_id="g1d-munich", site_id="munich",
    private_key_file="robot-key.json", platform_keys_file="platform-trust.json",
    safe_state=robot.inhibit, is_safe=robot.is_safe,
    max_permit_seconds=15, clock_uncertainty_seconds=0.1,
    command_timeout_seconds=0.2,
)
await session.start()
try:
    sample = session.latest
    if session.current(sample):
        output = await compute_control(sample.command)
        if session.current(sample):  # recheck after slow work
            robot.submit(output)
    await session.publish_rgb(rgb_uint8, name="g1d-ego-mono")
finally:
    await session.close()
```

Run the control loop continuously in your application. `safe_state` must inhibit
writes promptly while keeping the event loop responsive; `is_safe` must report completed
safe-state behavior. The application must reject malformed robot input, use
measured feedback, and fence pending output on simulator reset. `publish_rgb`
accepts true mono RGB, with fixed dimensions/name for the session. Rendering
belongs in a separate application task. The legacy camera API is unchanged.

The signed envelope is `{control: {session_id, ownership_version, sequence,
sent_at_ms}, command: {...}}` on `vr-controller-data`. The SDK exposes a latest
mailbox with one current sample. See `CONTROL_PROVENANCE.json` for the
pinned source of the authority implementation.

Validation: `uv sync --locked --group dev && uv run pytest -q`.

### Legacy minimal example

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

Atlas Core hosts the Quest interface at `/quest`. Use `RobotSession` for Atlas, with a registered robot identity and operator ownership. The legacy example requires its API-key backend.

#### Controller Mapping

- **Grip Button**: Sets reference frame when first pressed, enables position control when held
- **Trigger Button**: Controls gripper (pressed = open, released = closed)
- **Reset Button (X/A)**: Trigger to reset arm to initial position
- **Position Tracking**: Controller movement translates to arm movement when grip is held

The G1D development session in version 0.2.1 honors the platform's
`control_paused` state. Explicit resume waits for measured safe state; queued
pre-resume commands are rejected and local authority-stop fences remain in force.
`publish_rgb` requires the source host-monotonic `captured_at_ns`, retains that
clock in LiveKit, and rejects repeated, future or 100 ms-old captures. Browser
capture-to-display freshness still requires end-to-end qualification.

This release remains a development session protocol. It does not advertise the
Atlas v2 recording, upload or view-health capabilities and cannot pass that
release's compatibility admission. Capture timestamps alone do not establish
browser display freshness or recording retention.
