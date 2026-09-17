"""Control-permit payload shape.

Receivers verify signatures, context and bounded validity before authorizing control.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _identifier(value: str) -> str:
    if not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(
            "identifier must be nonblank without surrounding whitespace or control characters"
        )
    return value


Identifier = Annotated[str, AfterValidator(_identifier)]


class ControlPermit(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    permit_id: Identifier
    issuer: Identifier
    audience: Identifier
    site_id: Identifier
    robot_id: Identifier
    sdk_instance_id: Identifier
    session_id: Identifier
    operator_id: Identifier
    participant_identity: Identifier
    ownership_version: Annotated[int, Field(ge=1)]
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def utc_server_time(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def positive_validity(self) -> "ControlPermit":
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self
