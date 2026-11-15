"""Request schemas (Pydantic v2). Organization ids are never accepted from request bodies."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator, create_model

Lat = Field(None, ge=-90, le=90)
Lon = Field(None, ge=-180, le=180)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def partial(model: type[BaseModel], name: str) -> type[BaseModel]:
    """All-optional copy for PATCH. Use model_dump(exclude_unset=True)."""
    fields = {k: (Optional[f.annotation], None) for k, f in model.model_fields.items()}
    return create_model(name, __base__=Strict, **fields)  # type: ignore[call-overload]


class _GeoPair(Strict):
    @model_validator(mode="after")
    def _pairs(self):
        for a, b in (("latitude", "longitude"), ("pickup_lat", "pickup_lon"), ("drop_lat", "drop_lon")):
            if hasattr(self, a) and (getattr(self, a) is None) != (getattr(self, b) is None):
                raise ValueError(f"{a} and {b} must be provided together")
        return self


class LocationIn(_GeoPair):
    kind: Literal["branch", "office", "pickup_point", "drop_point", "depot"]
    name: str = Field(min_length=1, max_length=160)
    address: str = Field("", max_length=400)
    latitude: Optional[float] = Lat
    longitude: Optional[float] = Lon
    geocode_source: Optional[Literal["user", "geocoder", "trusted_import"]] = None
    is_active: bool = True


class ShiftIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    start_time: time
    end_time: time
    pickup_window_minutes: int = Field(30, ge=5, le=240)
    arrival_buffer_minutes: int = Field(15, ge=0, le=120)
    is_active: bool = True


class VehicleIn(Strict):
    vehicle_code: str = Field(min_length=1, max_length=40)
    registration_number: str = Field(min_length=3, max_length=20)
    vehicle_type: str = Field("sedan", max_length=40)
    capacity: int = Field(ge=1, le=60, description="Passenger seats, excluding the driver")
    accessibility_features: list[str] = Field(default_factory=list, max_length=10)
    availability: Literal["available", "unavailable", "maintenance"] = "available"
    insurance_valid_until: Optional[date] = None
    fitness_valid_until: Optional[date] = None
    default_route_id: Optional[uuid.UUID] = None
    default_driver_id: Optional[uuid.UUID] = None
    is_active: bool = True

    @field_validator("registration_number")
    @classmethod
    def _reg(cls, v: str) -> str:
        v = re.sub(r"\s+", "", v).upper()
        if not re.fullmatch(r"[A-Z0-9-]{3,20}", v):
            raise ValueError("registration_number must be alphanumeric")
        return v


class DriverIn(Strict):
    driver_code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    phone: Optional[str] = Field(None, pattern=r"^\+?[0-9 \-]{7,18}$")
    license_number: str = Field(min_length=3, max_length=40)
    license_valid_until: date
    is_available: bool = True
    eligible_shift_ids: list[uuid.UUID] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list, max_length=10)
    local_trip_eligible: bool = False
    user_id: Optional[uuid.UUID] = None
    is_active: bool = True


class EmployeeIn(_GeoPair):
    employee_code: str = Field(min_length=1, max_length=40)
    branch_id: Optional[uuid.UUID] = None
    name: str = Field(min_length=1, max_length=160)
    work_email: EmailStr
    phone: Optional[str] = Field(None, pattern=r"^\+?[0-9 \-]{7,18}$")
    department: Optional[str] = Field(None, max_length=80)
    designation: Optional[str] = Field(None, max_length=80)
    gender: Optional[Literal["male", "female", "other"]] = None
    shift_id: Optional[uuid.UUID] = None
    pickup_address: str = Field("", max_length=400)
    pickup_lat: Optional[float] = Lat
    pickup_lon: Optional[float] = Lon
    drop_address: str = Field("", max_length=400)
    drop_lat: Optional[float] = Lat
    drop_lon: Optional[float] = Lon
    preferred_pickup_time: Optional[time] = None
    accessibility_needs: list[str] = Field(default_factory=list, max_length=10)
    user_id: Optional[uuid.UUID] = None
    is_active: bool = True


LocationPatch = partial(LocationIn, "LocationPatch")
ShiftPatch = partial(ShiftIn, "ShiftPatch")
VehiclePatch = partial(VehicleIn, "VehiclePatch")
DriverPatch = partial(DriverIn, "DriverPatch")
EmployeePatch = partial(EmployeeIn, "EmployeePatch")


class RouteIn(Strict):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    branch_id: Optional[uuid.UUID] = None
    shift_id: Optional[uuid.UUID] = None
    stops: list[uuid.UUID] = Field(default_factory=list, max_length=60, description="Ordered location ids")
    is_active: bool = True


class ServiceDayIn(Strict):
    service_date: date
    is_service_day: bool = True
    note: str = Field("", max_length=200)


class OrganizationCreate(Strict):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    timezone: str = "Asia/Kolkata"
    admin_email: EmailStr
    admin_name: str = Field("", max_length=160)


class MemberIn(Strict):
    email: EmailStr
    full_name: str = Field("", max_length=160)
    role: Literal["org_admin", "dispatcher", "driver", "employee"]


class SettingsPatch(Strict):
    sos_gps_enabled: Optional[bool] = None
    live_tracking_enabled: Optional[bool] = None
    live_tracking_interval_seconds: Optional[int] = Field(None, ge=10, le=300)
    live_tracking_history_enabled: Optional[bool] = None
    live_tracking_history_retention_days: Optional[int] = Field(None, ge=1, le=365)
    live_tracking_employee_view: Optional[bool] = None
    sos_retention_days: Optional[int] = Field(None, ge=1, le=3650)
    max_detour_ratio: Optional[float] = Field(None, ge=1.0, le=5.0)
    max_trip_minutes: Optional[int] = Field(None, ge=10, le=480)
    require_dispatcher_confirmation: Optional[bool] = None
    driver_acceptance_timeout_minutes: Optional[int] = Field(None, ge=1, le=1440)
    last_drop_policy_enabled: Optional[bool] = None
    local_trip_target_per_day: Optional[int] = Field(None, ge=0, le=10)
    local_trip_max_km: Optional[float] = Field(None, gt=0, le=200)
    local_trips_excluded_from_km_fairness: Optional[bool] = None
    fairness_weights: Optional[dict[str, float]] = None
    fairness_window_days: Optional[int] = Field(None, ge=1, le=90)
    notification_channels: Optional[dict[str, bool]] = None
    boarding_code_required: Optional[bool] = None
    auto_dispatch_enabled: Optional[bool] = None
    auto_dispatch_cutoff: Optional[time] = None
    auto_dispatch_days_ahead: Optional[int] = Field(None, ge=1, le=3)
    auto_dispatch_send_to_drivers: Optional[bool] = None
    confirm: bool = Field(False, description="Required (true) when changing SOS GPS or live tracking switches")

    @field_validator("fairness_weights")
    @classmethod
    def _w(cls, v):
        if v is not None:
            if set(v) - {"planned_km", "trips", "duty_minutes", "deadhead_km"} or any(x < 0 or x > 10 for x in v.values()):
                raise ValueError("fairness_weights keys: planned_km, trips, duty_minutes, deadhead_km; values 0..10")
        return v

    @field_validator("notification_channels")
    @classmethod
    def _c(cls, v):
        if v is not None and set(v) - {"push", "whatsapp", "sms", "email"}:
            raise ValueError("unknown notification channel")
        return v


class FlagPatch(Strict):
    enabled: bool
    confirm: bool = False


class AllocateIn(Strict):
    service_date: date
    shift_id: uuid.UUID
    direction: Literal["pickup", "drop"]
    branch_id: Optional[uuid.UUID] = None
    dry_run: bool = True
    replace_proposed: bool = False
    generate_requests: bool = False


class RequestsGenerateIn(Strict):
    service_date: date
    shift_id: uuid.UUID
    direction: Literal["pickup", "drop"]


class MyRequestIn(Strict):
    service_date: date
    shift_id: Optional[uuid.UUID] = None
    direction: Literal["pickup", "drop"]
    requested_time: Optional[time] = None


class AllocationPatch(Strict):
    vehicle_id: Optional[uuid.UUID] = None
    driver_id: Optional[uuid.UUID] = None
    expected_version: int = Field(ge=1)


class PassengerAdd(Strict):
    request_id: uuid.UUID
    stop_order: Optional[int] = Field(None, ge=0)
    expected_version: Optional[int] = None


class ReasonIn(Strict):
    reason: str = Field(min_length=3, max_length=300)


class DriverResponseIn(Strict):
    accept: bool
    reason: Optional[str] = Field(None, max_length=300)


class LeaveIn(Strict):
    driver_id: Optional[uuid.UUID] = None   # staff may file on behalf of a driver; drivers omit it
    starts_on: date
    ends_on: date
    reason: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _order(self):
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self


class LeaveDecision(Strict):
    approve: bool
    note: Optional[str] = Field(None, max_length=300)


class RouteOverrideIn(Strict):
    service_date: date
    to_route_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=300)


class SosLocation(Strict):
    status: Literal["obtained", "denied", "unavailable"]
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0, le=100000)
    captured_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _obtained_needs_coords(self):
        if self.status == "obtained" and (self.latitude is None or self.longitude is None or self.captured_at is None):
            raise ValueError("status 'obtained' requires latitude, longitude and captured_at")
        return self


class SosCreate(Strict):
    message: str = Field("", max_length=500)
    allocation_id: Optional[uuid.UUID] = None
    location: Optional[SosLocation] = None


class SosResolve(Strict):
    note: Optional[str] = Field(None, max_length=500)


class PositionIn(Strict):
    allocation_id: uuid.UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0, le=100000)
    speed_kmh: Optional[float] = Field(None, ge=0, le=300)
    heading: Optional[float] = Field(None, ge=0, le=360)
    captured_at: datetime


class PushRegister(Strict):
    kind: Literal["web", "expo"]
    token: str = Field(min_length=10, max_length=600)


class PreferencesIn(Strict):
    push: bool = True
    whatsapp: bool = False
    sms: bool = False
    email: bool = False
