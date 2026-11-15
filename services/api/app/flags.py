"""Effective feature switches.

effective_sos_gps       = GPS_SOS_FEATURE_ENABLED (env) AND platform flag 'sos_gps' AND organization_settings.sos_gps_enabled
effective_live_tracking = LIVE_TRACKING_FEATURE_ENABLED (env) AND platform flag 'live_tracking' AND organization_settings.live_tracking_enabled

All three default to false. The env value is never read from the frontend.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .config import get_settings


@dataclass(frozen=True)
class FeatureState:
    deployment_enabled: bool
    platform_enabled: bool
    organization_enabled: bool

    @property
    def effective(self) -> bool:
        return self.deployment_enabled and self.platform_enabled and self.organization_enabled

    @property
    def blocked_by(self) -> str | None:
        if not self.deployment_enabled:
            return "deployment"
        if not self.platform_enabled:
            return "platform"
        if not self.organization_enabled:
            return "organization"
        return None

    def as_dict(self) -> dict:
        return {**asdict(self), "effective": self.effective, "blocked_by": self.blocked_by}


async def feature_states(conn: AsyncConnection, org_id: uuid.UUID) -> dict[str, FeatureState]:
    s = get_settings()
    flags = {r.key: r.enabled for r in (await conn.execute(text("SELECT key, enabled FROM platform_feature_flags"))).all()}
    row = (await conn.execute(text(
        "SELECT sos_gps_enabled, live_tracking_enabled FROM organization_settings WHERE organization_id = :o"), {"o": org_id})).first()
    return {
        "sos_gps": FeatureState(s.gps_sos_feature_enabled, flags.get("sos_gps", False), bool(row and row.sos_gps_enabled)),
        "live_tracking": FeatureState(s.live_tracking_feature_enabled, flags.get("live_tracking", False), bool(row and row.live_tracking_enabled)),
    }
