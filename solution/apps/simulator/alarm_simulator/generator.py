"""Deterministic synthetic alarm history generator.

Each simulated day uses its own RNG seeded from (seed, date, stream) so the
history for a past day never changes when ``data_end`` moves forward.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from alarm_simulator.catalog import (
    ASSET_INDEX,
    ASSETS,
    CAUSAL_PATTERNS,
    FLOOD_UNITS,
    SEVERITY_TO_PRIORITY,
    AlarmDef,
    Asset,
    alarm_def,
)

_ACK_RANGE = {"critical": (20, 300), "high": (45, 900), "medium": (90, 1800), "low": (300, 3600)}
_GROWTH_HORIZON_DAYS = 180.0


@dataclass
class Alarm:
    alarm_id: str
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    alarm_name: str
    alarm_code: str
    alarm_type: str
    severity: str
    priority: str
    tag: str
    status: str  # active | cleared
    acknowledged: bool
    start_time: datetime
    end_time: datetime | None
    ack_time: datetime | None
    value: float
    limit: float
    uom: str
    message: str

    @property
    def ack_delay_seconds(self) -> float | None:
        if self.ack_time is None:
            return None
        return (self.ack_time - self.start_time).total_seconds()

    def duration_seconds(self, now: datetime) -> float:
        end = self.end_time or now
        return max(0.0, (end - self.start_time).total_seconds())

    def to_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "alarm_id": self.alarm_id,
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "site": self.site,
            "unit": self.unit,
            "alarm_name": self.alarm_name,
            "alarm_code": self.alarm_code,
            "alarm_type": self.alarm_type,
            "severity": self.severity,
            "priority": self.priority,
            "tag": self.tag,
            "status": self.status,
            "acknowledged": self.acknowledged,
            "start_time": _iso(self.start_time),
            "end_time": _iso(self.end_time),
            "ack_time": _iso(self.ack_time),
            "ack_delay_seconds": self.ack_delay_seconds,
            "duration_seconds": round(self.duration_seconds(now), 1),
            "value": self.value,
            "limit": self.limit,
            "uom": self.uom,
            "message": self.message,
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _rng(seed: int, day: date, stream: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}|{day.isoformat()}|{stream}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


@dataclass
class _Raw:
    asset: Asset
    definition: AlarmDef
    start: datetime
    duration_s: float
    ack_delay_s: float | None


def _make_raw(
    rng: random.Random, asset: Asset, d: AlarmDef, start: datetime, *, chatter: bool = False
) -> _Raw:
    lo, hi = _ACK_RANGE[d.severity]
    roll = rng.random()
    if chatter:
        duration = rng.uniform(4, 55)
        ack = None if rng.random() < 0.6 else rng.uniform(5, 40)
    elif roll < 0.04:  # standing / stale alarm
        duration = rng.uniform(4 * 3600, 30 * 3600)
        ack = rng.uniform(lo, hi * 3)
    else:
        duration = rng.uniform(90, 75 * 60)
        ack = rng.uniform(lo, hi) if rng.random() > 0.03 else None
    if ack is not None and ack > duration and not chatter:
        duration = ack + rng.uniform(30, 600)
    return _Raw(asset, d, start, duration, ack)


def _background(seed: int, day: date, day_start: datetime) -> list[_Raw]:
    out: list[_Raw] = []
    for asset in ASSETS:
        rng = _rng(seed, day, f"bg:{asset.asset_id}")
        for d in _defs(asset):
            n = _poisson(rng, d.weekly_rate / 7.0)
            for _ in range(n):
                start = day_start + timedelta(seconds=rng.uniform(0, 86400))
                if d.chatter and rng.random() < 0.55:
                    burst = rng.randint(3, 8)
                    t = start
                    for _ in range(burst):
                        out.append(_make_raw(rng, asset, d, t, chatter=True))
                        t += timedelta(seconds=rng.uniform(40, 150))
                else:
                    out.append(_make_raw(rng, asset, d, start))
    return out


def _patterns(seed: int, day: date, day_start: datetime, growth_frac: float) -> list[_Raw]:
    out: list[_Raw] = []
    for pattern in CAUSAL_PATTERNS:
        rng = _rng(seed, day, f"pattern:{pattern.name}")
        rate = pattern.weekly_rate * (1.0 + pattern.growth * growth_frac) / 7.0
        for _ in range(_poisson(rng, rate)):
            t = day_start + timedelta(seconds=rng.uniform(0, 86400))
            for idx, (asset_id, code, min_lag, max_lag) in enumerate(pattern.chain):
                if idx > 0:
                    if rng.random() > pattern.completion:
                        break
                    t += timedelta(minutes=rng.uniform(min_lag, max_lag))
                asset = ASSET_INDEX[asset_id]
                out.append(_make_raw(rng, asset, alarm_def(asset.asset_type, code), t))
    return out


def _floods(seed: int, day: date, day_start: datetime) -> list[_Raw]:
    out: list[_Raw] = []
    for unit, weekly in FLOOD_UNITS.items():
        rng = _rng(seed, day, f"flood:{unit}")
        if rng.random() >= weekly / 7.0:
            continue
        unit_assets = [a for a in ASSETS if a.unit == unit]
        t0 = day_start + timedelta(seconds=rng.uniform(0, 86400 - 900))
        for _ in range(rng.randint(14, 28)):
            asset = rng.choice(unit_assets)
            d = rng.choice(_defs(asset))
            start = t0 + timedelta(seconds=rng.uniform(0, 8 * 60))
            out.append(_make_raw(rng, asset, d, start))
    return out


def _defs(asset: Asset) -> list[AlarmDef]:
    from alarm_simulator.catalog import ALARM_DEFS

    return ALARM_DEFS[asset.asset_type]


# Alarms that are still standing at the end of the history (drives "active alarm" questions).
_ACTIVE_AT_END: list[tuple[str, str, int, bool]] = [
    # (asset_id, alarm_code, minutes before end, acknowledged)
    ("NP-BFP-102", "BFP_DP_LO", 38, False),
    ("NP-BFP-102", "BFP_BRG_TEMP_HI", 125, True),
    ("NP-LOS-102", "LO_PRS_LO", 140, True),
    ("NP-BFP-101", "BFP_SP_LO", 22, False),
    ("NP-DEA-001", "DEA_LVL_LO", 29, True),
    ("ER-MTR-501", "MTR_TRIP", 14, False),
    ("ER-PMP-501", "PMP_DP_LO", 13, False),
    ("ER-FH-601", "FH_OUT_TEMP_HI", 55, True),
    ("ER-RX-601", "RX_DP_HI", 210, True),
    ("ER-MTR-503", "MTR_CUR_HI", 75, False),
    ("ER-FH-601", "FH_DRAFT_HI", 300, True),
    ("SP-CMP-201", "CMP_DP_HI", 46, True),
    ("SP-HX-201", "HX_OUT_TEMP_HI", 61, True),
]


def generate_history(seed: int, start: datetime, end: datetime) -> list[Alarm]:
    raws: list[_Raw] = []
    day = start.date()
    while datetime.combine(day, datetime.min.time(), UTC) < end:
        day_start = datetime.combine(day, datetime.min.time(), UTC)
        growth_frac = min(1.0, (day_start - start).days / _GROWTH_HORIZON_DAYS)
        raws.extend(_background(seed, day, day_start))
        raws.extend(_patterns(seed, day, day_start, growth_frac))
        raws.extend(_floods(seed, day, day_start))
        day += timedelta(days=1)

    raws = [r for r in raws if r.start < end]
    raws.sort(key=lambda r: (r.start, r.asset.asset_id, r.definition.alarm_code))

    alarms: list[Alarm] = []
    for raw in raws:
        alarms.append(_materialise(raw, end, seq=len(alarms) + 1, seed=seed))

    rng = random.Random(seed)
    for asset_id, code, minutes_before, acked in _ACTIVE_AT_END:
        asset = ASSET_INDEX[asset_id]
        d = alarm_def(asset.asset_type, code)
        st = end - timedelta(minutes=minutes_before)
        ack_delay = rng.uniform(*_ACK_RANGE[d.severity]) if acked else None
        raw = _Raw(asset, d, st, 0, ack_delay)
        alarm = _materialise(raw, end, seq=len(alarms) + 1, seed=seed, force_active=True)
        alarms.append(alarm)
    alarms.sort(key=lambda a: a.start_time)
    return alarms


def _materialise(raw: _Raw, end: datetime, *, seq: int, seed: int, force_active: bool = False) -> Alarm:
    d, asset = raw.definition, raw.asset
    raw.start = raw.start.replace(microsecond=0)
    rng = random.Random(seed * 1_000_003 + seq)
    deviation = rng.uniform(0.01, 0.12)
    value = d.limit * (1 + deviation) if d.direction == "high" else d.limit * (1 - deviation)
    if d.uom == "state":
        value = 1.0
    end_time = None if force_active else raw.start + timedelta(seconds=round(raw.duration_s))
    active = force_active or (end_time is not None and end_time > end)
    if active and not force_active:
        end_time = None
    ack_time = raw.start + timedelta(seconds=round(raw.ack_delay_s)) if raw.ack_delay_s is not None else None
    if ack_time is not None and ack_time > end:
        ack_time = None
    comparator = ">" if d.direction == "high" else "<"
    return Alarm(
        alarm_id=f"ALM-{seq:07d}",
        asset_id=asset.asset_id,
        asset_name=asset.asset_name,
        asset_type=asset.asset_type,
        site=asset.site,
        unit=asset.unit,
        alarm_name=d.alarm_name,
        alarm_code=d.alarm_code,
        alarm_type=d.alarm_type,
        severity=d.severity,
        priority=SEVERITY_TO_PRIORITY[d.severity],
        tag=f"{asset.tag_prefix}.{d.tag_suffix}",
        status="active" if active else "cleared",
        acknowledged=ack_time is not None,
        start_time=raw.start,
        end_time=end_time,
        ack_time=ack_time,
        value=round(value, 2),
        limit=d.limit,
        uom=d.uom,
        message=f"{d.alarm_name} on {asset.asset_name}: {round(value, 2)} {d.uom} {comparator} limit {d.limit} {d.uom}",
    )
