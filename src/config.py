from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from .policy import DEFAULT_POLICY_PATH


class Settings(BaseModel):
    # TYPESAFE_API_KEY is read by the TypeSafe SDK itself and never stored here.
    jev_model: str = Field(default="jev-1.14.0", description="Pinned, so tuned bands do not drift with jev-latest")
    typesafe_base_url: str | None = Field(default=None, description="Unset for api.typesafe.ai; set for a gateway")
    jev_timeout_s: float = Field(default=1.5, gt=0, description="Per attempt")
    jev_max_retries: int = Field(default=1, ge=0)
    system2_url: str | None = Field(default=None, description="Base URL of the system2/ reviewer; unset disables it")
    system2_timeout_s: float = Field(default=20.0, gt=0)
    policy_path: Path = DEFAULT_POLICY_PATH
    db_path: Path = Field(default_factory=lambda: Path(os.environ.get("JEVGATE_DB_PATH", ":memory:")))
    api_token: str | None = Field(default_factory=lambda: os.environ.get("JEVGATE_API_TOKEN"))

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        env = {
            "jev_model": os.getenv("JEV_MODEL"),
            "typesafe_base_url": os.getenv("TYPESAFE_BASE_URL"),
            "jev_timeout_s": os.getenv("JEV_TIMEOUT_S"),
            "jev_max_retries": os.getenv("JEV_MAX_RETRIES"),
            "system2_url": os.getenv("SYSTEM2_URL"),
            "system2_timeout_s": os.getenv("SYSTEM2_TIMEOUT_S"),
            "policy_path": os.getenv("JEVGATE_POLICY"),
            "db_path": os.getenv("JEVGATE_DB_PATH"),
            "api_token": os.getenv("JEVGATE_API_TOKEN"),
        }
        return cls(**{k: v for k, v in env.items() if v})
