from __future__ import annotations

from pydantic import BaseModel


class RoleConfig(BaseModel):
    """One role definition. Preset roles (CTO, Product, Engineering) ship
    as defaults in config/roles.yaml; users can also define their own
    custom roles/personalities at runtime and assign agents to them, so
    this doubles as the schema for a persisted, user-editable entity, not
    just static startup config - required for the upgradability goal and
    to match the Project Definition Form's custom role/personality feature.
    """

    id: str
    name: str
    tier: int
    model: str
    count: int = 1
    personality: str | None = None
    is_preset: bool = True
