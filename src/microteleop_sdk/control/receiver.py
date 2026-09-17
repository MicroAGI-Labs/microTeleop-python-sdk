"""Robot-local authority gate. Call tick independently of packet reception.

safe_state must synchronously inhibit control and initiate robot-specific safe handling.
A separate robot readiness acknowledgement is needed before the next ownership epoch.
"""

import json
import math
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from .crypto import AuthenticationError, TrustStore
from .permit import ControlPermit


class ControlReceiver:
    def __init__(
        self,
        trust: TrustStore,
        *,
        issuer,
        site_id,
        robot_id,
        sdk_instance_id,
        safe_state,
        max_permit_seconds,
        clock_uncertainty_seconds,
        command_timeout_seconds,
    ):
        if (
            not all(
                math.isfinite(v)
                for v in (
                    max_permit_seconds,
                    command_timeout_seconds,
                    clock_uncertainty_seconds,
                )
            )
            or max_permit_seconds <= 0
            or command_timeout_seconds <= 0
            or clock_uncertainty_seconds < 0
        ):
            raise ValueError("Invalid timing bounds")
        self.trust = trust
        self.context = {
            "issuer": issuer,
            "audience": "robot-control",
            "site_id": site_id,
            "robot_id": robot_id,
            "sdk_instance_id": sdk_instance_id,
        }
        self.safe_state = safe_state
        self.max_permit_seconds = max_permit_seconds
        self.uncertainty = clock_uncertainty_seconds
        self.command_timeout = command_timeout_seconds
        self.permit = None
        self.fenced_version = 0
        self.deadline = 0.0
        self.command_deadline = 0.0
        self.last_sequence = -1
        self.last_issued_at = None
        self._wall_anchor = time.time()
        self._monotonic_anchor = time.monotonic()

    def _wall_time(self):
        # The monotonic anchor bounds permit lifetime across wall-clock rollbacks.
        return max(time.time(), self._wall_anchor + time.monotonic() - self._monotonic_anchor)

    def accept_permit(self, token):
        self.tick()
        try:
            p = ControlPermit.model_validate_json(
                json.dumps(self.trust.verify(token, "control-permit"))
            )
        except (ValidationError, ValueError) as exc:
            raise AuthenticationError("Invalid control permit") from exc
        now = datetime.fromtimestamp(self._wall_time(), timezone.utc)
        if any(getattr(p, key) != value for key, value in self.context.items()):
            raise AuthenticationError("Wrong permit recipient or issuer")
        remaining = (p.expires_at - now).total_seconds() - self.uncertainty
        if (
            remaining <= 0
            or (p.issued_at - now).total_seconds() > self.uncertainty
            or (p.expires_at - p.issued_at).total_seconds() > self.max_permit_seconds
            or p.ownership_version <= self.fenced_version
        ):
            raise AuthenticationError("Expired, future, excessive or fenced permit")
        if self.permit:
            for field in (
                "ownership_version",
                "session_id",
                "operator_id",
                "participant_identity",
            ):
                if getattr(p, field) != getattr(self.permit, field):
                    raise AuthenticationError("Safe-state acknowledgement required before handover")
            if p.issued_at < self.last_issued_at:
                raise AuthenticationError("Stale renewal")
        else:
            self.last_sequence = -1
            self.command_deadline = time.monotonic() + self.command_timeout
        self.permit = p
        self.last_issued_at = p.issued_at
        self.deadline = time.monotonic() + remaining
        return p

    def stop(self, fence_version=None):
        p = self.permit
        self.fenced_version = max(
            self.fenced_version, fence_version or 0, p.ownership_version if p else 0
        )
        self.permit = None
        self.safe_state()

    def tick(self, monotonic_now=None):
        now = time.monotonic() if monotonic_now is None else monotonic_now
        if self.permit and (
            now >= self.deadline
            or now >= self.command_deadline
            or self._wall_time() + self.uncertainty >= self.permit.expires_at.timestamp()
        ):
            self.stop()
        return self.permit is not None

    def authorize(self, sender, session_id, ownership_version, sequence, sent_at_ms):
        if not self.tick():
            raise AuthenticationError("No current authority")
        p = self.permit
        if (
            sender != p.participant_identity
            or session_id != p.session_id
            or type(ownership_version) is not int
            or ownership_version != p.ownership_version
            or type(sequence) is not int
            or sequence <= self.last_sequence
            or sequence > 2**53 - 1
        ):
            raise AuthenticationError("Wrong sender, ownership or sequence")
        if type(sent_at_ms) not in (int, float) or not math.isfinite(sent_at_ms):
            raise AuthenticationError("Invalid command timestamp")
        age = self._wall_time() - sent_at_ms / 1000
        if age < -self.uncertainty or age + self.uncertainty > self.command_timeout:
            raise AuthenticationError("Command outside freshness bound")
        self.last_sequence = sequence
        self.command_deadline = time.monotonic() + self.command_timeout
