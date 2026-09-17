"""Robot-side client. Install on the robot; keep private-key files on that robot."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

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
        is_safe,
        max_permit_seconds,
        clock_uncertainty_seconds,
        command_timeout_seconds,
        http=None,
    ):
        if urlparse(platform_url).scheme != "https":
            raise ValueError("Robot authentication requires HTTPS")
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
        }
        c = self._post("challenges", body)
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
        self.version = result["ownership_version"]
        return result

    def connect(self, node_id="robot"):
        return self.exchange("connect", node_id)

    def process_state(self, result):
        """Call on the robot control thread, never a networking worker."""
        if result["stop"]:
            self.guard.stop(result["ownership_version"])
        elif result.get("permit") and result["ownership_version"] > self.guard.fenced_version:
            # Never resurrect a locally stopped epoch while the platform catches up.
            self.guard.accept_permit(result["permit"])
        if self.guard.permit is None and (result["stop"] or not result["ready"]):
            self.guard.stop(result["ownership_version"])
            return self.is_safe() is True
        return False

    def poll(self):
        result = self.exchange("poll")
        if self.process_state(result):
            result = self.exchange("safe")
        return result
