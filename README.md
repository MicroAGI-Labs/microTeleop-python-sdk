# microTeleop Python SDK 0.3.0

`RobotSession` is the supported robot API. Install this release independently of Atlas Core.
Atlas owns identity, operator admission and permits; the SDK owns signed challenge/proof,
LiveKit transport, the bounded latest-command mailbox, watchdog and source camera timestamps.
The robot controller owns measured safe state and checks `session.current(sample)` again
after computation, immediately before output.

Configure `compatibility` with the exact deployed Atlas hello: protocol_version 2,
sdk_version 0.3.0, sdk_sha (the immutable release commit supplied by the deployment),
capabilities `["signed-control-v1", "view-health-v2", "camera-source-timestamp-v1"]`,
and profile_sha256. Configure contract_digest separately from the exported Atlas contract.
The SDK does not compute or embed its own future commit hash. Atlas pins the committed
release and wheel; mismatched admission is rejected before signing a proof.

```python
from microteleop_sdk import RobotSession

session = RobotSession(
    platform_url="https://atlas.example", robot_id="g1d", site_id="munich",
    private_key_file="robot.pem", platform_keys_file="platform-keys.json",
    compatibility=config["compatibility"], contract_digest=config["contract_digest"],
    safe_state=controller.inhibit, is_safe=controller.is_safe,
    max_permit_seconds=15, clock_uncertainty_seconds=0.001,
    command_timeout_seconds=0.1,
)
```

Use `await session.start()`, `session.latest`, `session.current(sample)` and
`await session.close()`. Publish RGB via `await session.publish_rgb(rgb,
captured_at_ns=source_monotonic_ns, name="front")`. Capture time must come from
the camera source and remain fresh through publication. Local publication is
independent of authenticated decoded-view health; stale health fences authority.
Pause requires measured safe state and explicit platform resume. Reconnect changes
the SDK boot identity and cannot restore a prior permit. Certificate verification
is mandatory. Current platform permits use schema 1; admission hello uses protocol 2.

Recording/upload, live Quest/LiveKit trials, Genesis dynamics and hardware acceptance
are separate qualification facts. This release does not advertise recording/upload
capabilities. Test with `uv run --python 3.12 --group dev pytest`.
