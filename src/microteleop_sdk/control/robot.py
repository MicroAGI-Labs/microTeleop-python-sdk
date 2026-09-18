"""Robot-side client. Install on the robot; keep private-key files on that robot."""

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .contracts import AuthorityHealth, ChallengeResponse, CompatibilityHello
from .crypto import AuthenticationError, SigningKey, TrustStore
from .receiver import ControlReceiver


class RobotControlClient:
    def __init__(
        self,
        *,
        platform_url,
        robot_id,
        site_id,
        private_key_file,
        platform_keys_file,
        safe_state,
        is_safe: Callable[[], bool],
        max_permit_seconds,
        clock_uncertainty_seconds,
        command_timeout_seconds,
        compatibility,
        contract_digest,
        recover_command_gaps=False,
        http=None,
    ):
        if urlparse(platform_url).scheme != "https":
            raise ValueError("Robot authentication requires HTTPS")
        self.compatibility = CompatibilityHello.model_validate_json(json.dumps(compatibility))
        if (
            not isinstance(contract_digest, str)
            or len(contract_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in contract_digest)
        ):
            raise ValueError("expected pinned contract digest")
        self.contract_digest = contract_digest
        self.health_deadline = 0.0
        self.health_identity = None
        self.platform_url = platform_url.rstrip("/")
        self.robot_id = robot_id
        self.key = SigningKey.load(private_key_file)
        self.trust = TrustStore(json.loads(Path(platform_keys_file).read_text()))
        self.instance = str(uuid.uuid4())
        self.http = http or requests.Session()
        self.is_safe = is_safe
        self.version = 0
        self.guard = ControlReceiver(
            self.trust,
            issuer=self.platform_url,
            site_id=site_id,
            robot_id=robot_id,
            sdk_instance_id=self.instance,
            safe_state=safe_state,
            max_permit_seconds=max_permit_seconds,
            clock_uncertainty_seconds=clock_uncertainty_seconds,
            command_timeout_seconds=command_timeout_seconds,
            recover_command_gaps=recover_command_gaps,
        )
        self.guard.stop()

    def _post(self, path, body):
        response = self.http.post(
            self.platform_url + "/api/control/" + path,
            json=body,
            timeout=5,
            verify=True,
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise AuthenticationError("Unexpected authentication response")
        return response.json()

    def exchange(self, operation, node_id="robot"):
        body = {
            "robot_id": self.robot_id,
            "key_id": self.key.key_id,
            "sdk_instance_id": self.instance,
            "operation": operation,
            "node_id": node_id,
            "ownership_version": self.version,
            "compatibility": self.compatibility.model_dump(mode="json"),
            "video_providers": ["livekit"],
        }
        try:
            c = ChallengeResponse.model_validate(self._post("challenges", body))
            if (
                not c.compatibility.accepted
                or c.compatibility.reason is not None
                or c.compatibility.contract_digest != self.contract_digest
            ):
                raise AuthenticationError("Platform compatibility result differs")
        except ValueError:
            self.guard.stop()
            raise
        c = c.model_dump(mode="json")
        signed = self.trust.verify(c["challenge"], "robot-challenge")
        now = datetime.now(timezone.utc)
        if (
            any(signed.get(k) != v for k, v in body.items())
            or signed.get("issuer") != self.platform_url
            or signed.get("challenge_id") != c["challenge_id"]
            or datetime.fromisoformat(signed["expires_at"]) <= now
            or datetime.fromisoformat(signed["issued_at"]).timestamp()
            > time.time() + self.guard.uncertainty
        ):
            raise AuthenticationError("Invalid challenge context or expiry")
        result = self._post(
            "proofs",
            {
                "challenge_id": c["challenge_id"],
                "proof": self.key.sign({"challenge": c["challenge"]}, "robot-proof"),
            },
        )
        self.validate_state(result)
        if result["operation"] != operation or result["node_id"] != node_id:
            self.guard.stop()
            raise AuthenticationError("Authority operation differs")
        self.version = result["ownership_version"]
        return result

    def connect(self, node_id="robot"):
        return self.exchange("connect", node_id)

    def validate_state(self, result):
        try:
            state = AuthorityHealth.model_validate_json(json.dumps(result))
            if (
                state.robot_id != self.robot_id
                or state.site_id != self.guard.context["site_id"]
                or state.sdk_instance_id != self.instance
                or state.profile_sha256 not in (None, self.compatibility.profile_sha256)
            ):
                raise AuthenticationError("Authority binding differs")
            if state.permit is not None:
                permit = self.trust.verify(state.permit, "control-permit")
                if (
                    any(
                        permit.get(key) != getattr(state, key)
                        for key in (
                            "robot_id",
                            "site_id",
                            "sdk_instance_id",
                            "session_id",
                            "participant_identity",
                            "ownership_version",
                        )
                    )
                    or state.profile_sha256 != self.compatibility.profile_sha256
                ):
                    raise AuthenticationError("Authority permit binding differs")
            if not state.control_paused and state.permit is not None and not state.stop:
                remaining = (
                    min(
                        state.healthy_until(datetime.now(timezone.utc)),
                        state.healthy_until(state.server_time),
                    )
                    - self.guard.uncertainty
                )
                if remaining <= 0:
                    raise AuthenticationError("Required view health expired")
                identity = (state.ownership_version, state.health_sequence)
                deadline = time.monotonic() + remaining
                if self.health_identity is not None and identity < self.health_identity:
                    raise AuthenticationError("View health regressed")
                if identity == self.health_identity:
                    deadline = min(deadline, self.health_deadline)
                self.health_identity, self.health_deadline = identity, deadline
            return state
        except (ValueError, TypeError):
            self.guard.stop()
            raise

    def tick(self):
        if self.guard.permit is not None and not self.guard.paused:
            if time.monotonic() >= self.health_deadline:
                self.guard.stop()
        return self.guard.tick()

    def process_state(self, result):
        """Process authority updates on the robot control thread."""
        self.validate_state(result)
        paused = result["control_paused"]
        if type(paused) is not bool:
            self.guard.stop()
            raise AuthenticationError("Invalid platform pause state")
        if paused:
            self.guard.pause()
        if result["stop"]:
            self.guard.stop(result["ownership_version"])
        elif result.get("permit") and result["ownership_version"] > self.guard.fenced_version:
            # Preserve local stop fencing while the platform catches up.
            self.guard.accept_permit(result["permit"])
            if not paused and self.guard.paused and self.is_safe() is True:
                self.guard.resume()
        if self.guard.permit is None and (result["stop"] or not result["ready"]):
            self.guard.stop(result["ownership_version"])
            return self.is_safe() is True
        return False

    def poll(self):
        result = self.exchange("poll")
        if self.process_state(result):
            result = self.exchange("safe")
        return result
