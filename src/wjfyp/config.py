from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from wjfyp.models.agent import RoleConfig

# Repo-root-relative by default. Callers running from elsewhere (tests,
# a packaged install) should pass explicit paths instead of relying on
# these constants.
DEFAULT_ROLES_PATH = Path("config/roles.yaml")
DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")


class EventLogConfig(BaseModel):
    backend: Literal["sqlite"] = "sqlite"
    path: str = "data/eventlog.db"


class Settings(BaseModel):
    retry_cap: int = 3
    concurrency: Literal["sequential"] = "sequential"
    autonomy_mode: Literal["autonomous", "intervention"] = "autonomous"
    event_log: EventLogConfig = EventLogConfig()


def load_roles(path: Path = DEFAULT_ROLES_PATH) -> list[RoleConfig]:
    """Load the preset role team from config/roles.yaml.

    This only ever returns preset roles (is_preset=True in the file).
    User-created custom roles live in the persisted role store, not this
    file - see the custom roles/personalities feature in
    cs3ip-fyp-overview memory. Loading both into one list is the caller's
    job once that store exists.
    """
    raw = yaml.safe_load(path.read_text())
    return [RoleConfig(**entry) for entry in raw["roles"]]


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    raw = yaml.safe_load(path.read_text())
    return Settings(**raw)
