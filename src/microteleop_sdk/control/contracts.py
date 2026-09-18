"""Current Atlas challenge and robot authority wire contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
CAPABILITIES = ("signed-control-v1", "view-health-v2", "camera-source-timestamp-v1")


class Boundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class CompatibilityHello(Boundary):
    protocol_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    sdk_version: Literal["0.3.0"]
    sdk_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    capabilities: tuple[Identifier, ...]
    profile_sha256: Digest

    @model_validator(mode="after")
    def supported(self):
        if len(self.capabilities) != len(CAPABILITIES) or set(self.capabilities) != set(
            CAPABILITIES
        ):
            raise ValueError("unsupported SDK capabilities")
        return self


class CompatibilityResult(Boundary):
    protocol_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    accepted: bool
    reason: Identifier | None
    contract_digest: Digest


class ChallengeResponse(Boundary):
    challenge_id: Identifier
    challenge: Annotated[str, Field(min_length=1, max_length=16384)]
    compatibility: CompatibilityResult


class AuthorityHealth(Boundary):
    robot_id: Identifier
    room_name: Identifier
    session_id: Identifier | None
    participant_identity: Identifier | None
    ownership_version: Annotated[int, Field(ge=0)]
    permit: str | None
    expires_at: AwareDatetime | None
    server_time: AwareDatetime
    permit_seconds: Annotated[int, Field(gt=0)]
    profile_sha256: Digest | None
    control_paused: bool
    pause_reason: str | None
    required_views_fresh: bool
    optional_view_warnings: list[str]
    health_sequence: Annotated[int, Field(ge=0)]
    health_expires_at: AwareDatetime | None
    transport: Literal["livekit"]
    transport_status: Literal["accepted"]
    video: dict[Literal["provider"], Literal["livekit"]]
    operation: Literal["connect", "poll", "safe"]
    node_id: Identifier
    sdk_instance_id: Identifier
    stop: bool
    ready: bool
    site_id: Identifier
    token: str | None = None
    livekit_url: str | None = None
    protocol_server_url: str | None = None

    def healthy_until(self, now: datetime) -> float:
        if (
            not self.required_views_fresh
            or self.health_sequence < 1
            or self.health_expires_at is None
        ):
            return 0.0
        return (self.health_expires_at - now).total_seconds()
