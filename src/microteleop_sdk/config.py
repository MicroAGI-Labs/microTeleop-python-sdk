from dataclasses import dataclass

DEFAULT_BACKEND_URL = "https://atlas-core-api-f2qyzkorqq-oa.a.run.app"


@dataclass
class MicroTeleopConfig:
    backend_url: str = DEFAULT_BACKEND_URL
    controller_participant: str = "controllers-processing"
    camera_participant: str = "camera_streamer"
