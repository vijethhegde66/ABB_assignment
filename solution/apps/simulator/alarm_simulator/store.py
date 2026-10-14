"""In-memory alarm repository and analytics engine.

All filtering is done over typed Python objects (no string-built queries), so
the simulator is not exposed to injection through query parameters.
"""

from __future__ import annotations

import bisect
import re
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from alarm_simulator.catalog import ASSET_INDEX, ASSETS, SEVERITY_ORDER, Asset
from alarm_simulator.generator import Alarm, _iso, generate_history


class NotFound(Exception):
    def __init__(self, what: str, ident: str) -> None:
        super().__init__(f"{what} '{ident}' not found")
        self.what = what
        self.ident = ident


class InvalidRequest(Exception):
    pass


@dataclass
class Scope:
    asset_ids: list[str] | None = None
    site: str | None = None
    unit: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    severities: list[str] | None = None
    min_severity: str | None = None
    alarm_types: list[str] | None = None
    status: str | None = None
    alarm_names: list[str] | None = None


SORTABLE = {
    "start_time",
    "severity",
    "priority",
    "asset_name",
    "alarm_name",
    "ack_delay_seconds",
    "duration_seconds",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class AlarmStore:
    def __init__(self, seed: int, start: datetime, end: datetime) -> None:
        self.seed = seed
        self.start = start
        self.end = end
        self.alarms: list[Alarm] = generate_history(seed, start, end)
        self.by_id: dict[str, Alarm] = {a.alarm_id: a for a in self.alarms}
        self._calculations: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ assets
    def search_assets(
        self,
        query: str,
        site: str | None = None,
        unit: str | None = None,
        asset_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        q = _norm(query)
        q_tokens = q.split()
        results: list[tuple[float, Asset]] = []
        for asset in ASSETS:
            if site and asset.site.lower() != site.lower():
                continue
            if unit and asset.unit.lower() != unit.lower():
                continue
            if asset_type and asset.asset_type != asset_type:
                continue
            name = _norm(asset.asset_name)
            haystack = f"{name} {_norm(asset.asset_id)} {_norm(asset.asset_type)} {_norm(asset.tag_prefix)}"
            if not q_tokens:
                score = 0.1
            elif q == name or q == _norm(asset.asset_id):
                score = 1.0
            else:
                hay_tokens = set(haystack.split())
                matched = sum(
                    1 for t in q_tokens if t in hay_tokens or any(h.startswith(t) for h in hay_tokens)
                )
                if matched == 0:
                    continue
                # numbers in the query (e.g. "101") must match exactly when present
                numbers = [t for t in q_tokens if t.isdigit()]
                if numbers and not all(n in hay_tokens for n in numbers):
                    continue
                score = 0.4 * matched / len(q_tokens) + 0.5 * (1.0 if name.startswith(q) else 0.0)
                score += 0.1 * (matched / max(1, len(name.split())))
            results.append((round(min(score, 0.99 if score < 1 else 1.0), 3), asset))
        results.sort(key=lambda r: (-r[0], r[1].asset_name))
        return [
            {
                "asset_id": a.asset_id,
                "asset_name": a.asset_name,
                "asset_type": a.asset_type,
                "site": a.site,
                "unit": a.unit,
                "criticality": a.criticality,
                "match_score": s,
            }
            for s, a in results[:limit]
        ]

    def asset(self, asset_id: str) -> Asset:
        asset = ASSET_INDEX.get(asset_id)
        if asset is None:
            raise NotFound("asset", asset_id)
        return asset

    def asset_metadata(self, asset_id: str) -> dict[str, Any]:
        a = self.asset(asset_id)
        return {
            "asset_id": a.asset_id,
            "asset_name": a.asset_name,
            "asset_type": a.asset_type,
            "description": a.description,
            "site": a.site,
            "unit": a.unit,
            "criticality": a.criticality,
            "manufacturer": a.manufacturer,
            "model": a.model,
            "install_date": a.install_date,
            "tag_prefix": a.tag_prefix,
            "operating_limits": a.operating_limits,
            "related_assets": [
                {
                    "asset_id": rid,
                    "asset_name": ASSET_INDEX[rid].asset_name,
                    "asset_type": ASSET_INDEX[rid].asset_type,
                    "relationship": rel,
                }
                for rid, rel in a.related
            ],
            "procedure_refs": list(a.procedures),
        }

    # ------------------------------------------------------------------ alarms
    def filter(self, scope: Scope) -> list[Alarm]:
        lo = 0
        hi = len(self.alarms)
        if scope.start is not None:
            lo = bisect.bisect_left(self.alarms, scope.start, key=lambda a: a.start_time)
        if scope.end is not None:
            hi = bisect.bisect_left(self.alarms, scope.end, key=lambda a: a.start_time)
        asset_ids = set(scope.asset_ids) if scope.asset_ids else None
        sev = set(scope.severities) if scope.severities else None
        min_sev = SEVERITY_ORDER[scope.min_severity] if scope.min_severity else None
        types = set(scope.alarm_types) if scope.alarm_types else None
        names = {n.lower() for n in scope.alarm_names} if scope.alarm_names else None
        out: list[Alarm] = []
        for a in self.alarms[lo:hi]:
            if asset_ids is not None and a.asset_id not in asset_ids:
                continue
            if scope.site and a.site.lower() != scope.site.lower():
                continue
            if scope.unit and a.unit.lower() != scope.unit.lower():
                continue
            if sev is not None and a.severity not in sev:
                continue
            if min_sev is not None and SEVERITY_ORDER[a.severity] < min_sev:
                continue
            if types is not None and a.alarm_type not in types:
                continue
            if scope.status and a.status != scope.status:
                continue
            if names is not None and a.alarm_name.lower() not in names:
                continue
            out.append(a)
        return out

    def list_alarms(
        self, scope: Scope, page: int, page_size: int, sort_by: str, sort_order: str
    ) -> dict[str, Any]:
        if sort_by not in SORTABLE:
            raise InvalidRequest(f"sort_by must be one of {sorted(SORTABLE)}")
        rows = self.filter(scope)
        reverse = sort_order == "desc"

        def key(a: Alarm) -> Any:
            if sort_by == "severity":
                return SEVERITY_ORDER[a.severity]
            if sort_by == "ack_delay_seconds":
                return a.ack_delay_seconds if a.ack_delay_seconds is not None else -1
            if sort_by == "duration_seconds":
                return a.duration_seconds(self.end)
            return getattr(a, sort_by)

        rows.sort(key=key, reverse=reverse)
        total = len(rows)
        total_pages = max(1, -(-total // page_size))
        start = (page - 1) * page_size
        data = [a.to_dict(self.end) for a in rows[start : start + page_size]]
        return {
            "data": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1,
            },
        }

    def alarm(self, alarm_id: str) -> Alarm:
        a = self.by_id.get(alarm_id)
        if a is None:
            raise NotFound("alarm", alarm_id)
        return a

    # ------------------------------------------------------------------ analytics
    @staticmethod
    def _recurring_flags(rows: list[Alarm], window: timedelta = timedelta(hours=24)) -> dict[str, bool]:
        """An alarm is 'recurring' if the same alarm on the same asset fired within the previous 24h."""
        last_seen: dict[tuple[str, str], datetime] = {}
        flags: dict[str, bool] = {}
        for a in sorted(rows, key=lambda x: x.start_time):
            k = (a.asset_id, a.alarm_code)
            prev = last_seen.get(k)
            flags[a.alarm_id] = prev is not None and a.start_time - prev <= window
            last_seen[k] = a.start_time
        return flags

    def _kpis(self, rows: list[Alarm], recurring: dict[str, bool], wanted: Iterable[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        n = len(rows)
        acks = [a.ack_delay_seconds for a in rows if a.ack_delay_seconds is not None]
        for k in wanted:
            if k == "alarm_count":
                out[k] = n
            elif k == "recurring_rate":
                out[k] = round(sum(1 for a in rows if recurring.get(a.alarm_id)) / n, 3) if n else 0.0
            elif k == "avg_ack_delay":
                out["avg_ack_delay_seconds"] = round(statistics.fmean(acks), 1) if acks else None
            elif k == "critical_count":
                out[k] = sum(1 for a in rows if a.severity == "critical")
            elif k == "chattering_count":
                out[k] = sum(1 for a in rows if a.duration_seconds(self.end) < 60)
            elif k == "standing_count":
                out[k] = sum(1 for a in rows if a.duration_seconds(self.end) > 3 * 3600)
            elif k == "suppression_candidate_rate":
                cand = sum(
                    1
                    for a in rows
                    if a.duration_seconds(self.end) < 60 or a.duration_seconds(self.end) > 3 * 3600
                )
                out[k] = round(cand / n, 3) if n else 0.0
            elif k == "unacknowledged_count":
                out[k] = sum(1 for a in rows if not a.acknowledged)
        return out

    def summary(self, scope: Scope, group_by: list[str], kpis: list[str]) -> dict[str, Any]:
        rows = self.filter(scope)
        recurring = self._recurring_flags(rows)
        groups: dict[tuple[Any, ...], list[Alarm]] = defaultdict(list)
        for a in rows:
            groups[tuple(getattr(a, g) for g in group_by)].append(a)
        group_rows = []
        for key, members in groups.items():
            entry: dict[str, Any] = {"group": dict(zip(group_by, key, strict=True))}
            entry.update(self._kpis(members, recurring, kpis or ["alarm_count"]))
            entry.setdefault("alarm_count", len(members))
            entry["first_occurrence"] = _iso(min(m.start_time for m in members))
            entry["last_occurrence"] = _iso(max(m.start_time for m in members))
            entry["max_severity"] = max((m.severity for m in members), key=SEVERITY_ORDER.__getitem__)
            group_rows.append(entry)
        group_rows.sort(key=lambda g: -g["alarm_count"])
        overall = self._kpis(rows, recurring, ["alarm_count", *kpis])
        return {"total_alarms": len(rows), "kpis": overall, "groups": group_rows}

    def trends(self, scope: Scope, bucket: str, metrics: list[str]) -> dict[str, Any]:
        rows = self.filter(scope)
        step = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7)}[bucket]
        start = scope.start or self.start
        end = scope.end or self.end
        n_buckets = max(1, int(-(-(end - start) // step)))
        if n_buckets > 5000:
            raise InvalidRequest("too many buckets; widen the bucket size or narrow the time range")
        buckets: list[list[Alarm]] = [[] for _ in range(n_buckets)]
        for a in rows:
            idx = int((a.start_time - start) // step)
            if 0 <= idx < n_buckets:
                buckets[idx].append(a)
        series = []
        for i, members in enumerate(buckets):
            point: dict[str, Any] = {"bucket_start": _iso(start + i * step)}
            acks = [m.ack_delay_seconds for m in members if m.ack_delay_seconds is not None]
            for m in metrics:
                if m == "alarm_count":
                    point["alarm_count"] = len(members)
                elif m == "avg_ack_delay":
                    point["avg_ack_delay_seconds"] = round(statistics.fmean(acks), 1) if acks else None
                elif m == "critical_count":
                    point["critical_count"] = sum(1 for x in members if x.severity == "critical")
            series.append(point)
        counts = [len(b) for b in buckets]
        slope = _slope(counts)
        mean = statistics.fmean(counts) if counts else 0.0
        rel = slope * len(counts) / mean if mean else 0.0
        direction = "increasing" if rel > 0.25 else "decreasing" if rel < -0.25 else "stable"
        half = len(counts) // 2
        return {
            "bucket": bucket,
            "series": series,
            "trend": {
                "direction": direction,
                "slope_per_bucket": round(slope, 4),
                "relative_change_over_range": round(rel, 3),
                "first_half_count": sum(counts[:half]),
                "second_half_count": sum(counts[half:]),
            },
        }

    def correlation(
        self,
        asset_ids: list[str],
        start: datetime,
        end: datetime,
        lag_window_minutes: int,
        severity_threshold: str,
        min_support: int,
        include_related: bool,
        method: str,
    ) -> dict[str, Any]:
        for aid in asset_ids:
            self.asset(aid)
        scope_assets = set(asset_ids)
        if include_related:
            for aid in asset_ids:
                scope_assets.update(rid for rid, _ in ASSET_INDEX[aid].related)
        rows = self.filter(
            Scope(asset_ids=sorted(scope_assets), start=start, end=end, min_severity=severity_threshold)
        )
        by_key: dict[tuple[str, str], list[datetime]] = defaultdict(list)
        names: dict[tuple[str, str], tuple[str, str]] = {}
        for a in rows:
            k = (a.asset_id, a.alarm_code)
            by_key[k].append(a.start_time)
            names[k] = (a.asset_name, a.alarm_name)
        window = timedelta(minutes=lag_window_minutes)
        total_minutes = max(1.0, (end - start).total_seconds() / 60)
        pairs: list[dict[str, Any]] = []
        keys = list(by_key)
        for src in keys:
            src_times = by_key[src]
            for tgt in keys:
                if tgt == src:
                    continue
                tgt_times = by_key[tgt]
                support = 0
                lags: list[float] = []
                for t in src_times:
                    i = bisect.bisect_right(tgt_times, t)
                    if i < len(tgt_times) and tgt_times[i] - t <= window:
                        support += 1
                        lags.append((tgt_times[i] - t).total_seconds() / 60)
                if support < min_support or support < 2:
                    continue
                confidence = support / len(src_times)
                p_tgt = min(1.0, len(tgt_times) * lag_window_minutes / total_minutes)
                lift = confidence / p_tgt if p_tgt else 0.0
                if method == "cooccurrence" or lift >= 1.5:
                    pairs.append(
                        {
                            "source": {
                                "asset_id": src[0],
                                "asset_name": names[src][0],
                                "alarm_code": src[1],
                                "alarm_name": names[src][1],
                            },
                            "target": {
                                "asset_id": tgt[0],
                                "asset_name": names[tgt][0],
                                "alarm_code": tgt[1],
                                "alarm_name": names[tgt][1],
                            },
                            "support": support,
                            "confidence": round(confidence, 3),
                            "lift": round(lift, 2),
                            "avg_lag_minutes": round(statistics.fmean(lags), 1),
                        }
                    )
        pairs.sort(
            key=lambda p: (-(p["support"] * p["confidence"] * min(p["lift"], 50)), p["source"]["alarm_code"])
        )
        pairs = [p for p in pairs if p["lift"] >= 1.2][:25]
        # leading indicators: sources that precede alarms on the requested assets
        leading: Counter[tuple[str, str]] = Counter()
        for p in pairs:
            if p["target"]["asset_id"] in asset_ids:
                leading[(p["source"]["asset_id"], p["source"]["alarm_name"])] += p["support"]
        return {
            "method": method,
            "scope_asset_ids": sorted(scope_assets),
            "events_analyzed": len(rows),
            "pairs": pairs,
            "leading_indicators": [
                {
                    "asset_id": k[0],
                    "asset_name": ASSET_INDEX[k[0]].asset_name,
                    "alarm_name": k[1],
                    "support": v,
                }
                for k, v in leading.most_common(8)
            ],
        }

    def flood_analysis(
        self, scope: Scope, threshold_count: int, rolling_window_minutes: int
    ) -> dict[str, Any]:
        rows = self.filter(scope)
        times = [a.start_time for a in rows]
        window = timedelta(minutes=rolling_window_minutes)
        windows: list[dict[str, Any]] = []
        i = 0
        j = 0
        n = len(times)
        while i < n:
            while j < n and times[j] - times[i] <= window:
                j += 1
            count = j - i
            if count >= threshold_count:
                members = rows[i:j]
                # extend the flood while the rate stays above threshold
                end_idx = j
                while end_idx < n and times[end_idx] - times[end_idx - threshold_count + 1] <= window:
                    end_idx += 1
                members = rows[i:end_idx]
                top = Counter(m.alarm_name for m in members).most_common(5)
                windows.append(
                    {
                        "start": _iso(times[i]),
                        "end": _iso(times[end_idx - 1]),
                        "alarm_count": len(members),
                        "peak_rate_per_10min": count,
                        "assets_involved": sorted({m.asset_name for m in members}),
                        "top_alarms": [{"alarm_name": k, "count": v} for k, v in top],
                    }
                )
                i = end_idx
                j = max(j, i)
            else:
                i += 1
        flood_alarms = sum(w["alarm_count"] for w in windows)
        return {
            "threshold_count": threshold_count,
            "rolling_window_minutes": rolling_window_minutes,
            "total_alarms": len(rows),
            "flood_windows": windows,
            "flood_count": len(windows),
            "percent_alarms_in_flood": round(100 * flood_alarms / len(rows), 2) if rows else 0.0,
        }

    def rationalization_candidates(
        self, scope: Scope, recurrence_threshold: int, stale_minutes_threshold: int
    ) -> dict[str, Any]:
        rows = self.filter(scope)
        groups: dict[tuple[str, str], list[Alarm]] = defaultdict(list)
        for a in rows:
            groups[(a.asset_id, a.alarm_code)].append(a)
        candidates: list[dict[str, Any]] = []
        for (asset_id, _code), members in groups.items():
            reasons = []
            chatter = sum(1 for m in members if m.duration_seconds(self.end) < 60)
            stale = sum(1 for m in members if m.duration_seconds(self.end) > stale_minutes_threshold * 60)
            unacked = sum(1 for m in members if not m.acknowledged)
            if len(members) >= recurrence_threshold:
                reasons.append("frequent")
            if chatter >= 3 and chatter / len(members) >= 0.3:
                reasons.append("chattering")
            if stale >= 2:
                reasons.append("stale")
            if unacked / len(members) > 0.5 and len(members) >= 3:
                reasons.append("often_unacknowledged")
            if not reasons:
                continue
            rec = []
            if "chattering" in reasons:
                rec.append("Review deadband / on-off delay (e.g. 2-5 s delay, 1-2% deadband).")
            if "stale" in reasons:
                rec.append("Review whether alarm is a standing condition; consider state-based suppression.")
            if "frequent" in reasons:
                rec.append(
                    "Verify setpoint against operating envelope and investigate underlying process cause."
                )
            if "often_unacknowledged" in reasons:
                rec.append(
                    "Confirm the alarm requires an operator response; otherwise demote to event/alert."
                )
            m0 = members[0]
            candidates.append(
                {
                    "asset_id": asset_id,
                    "asset_name": m0.asset_name,
                    "alarm_name": m0.alarm_name,
                    "alarm_code": m0.alarm_code,
                    "severity": m0.severity,
                    "occurrences": len(members),
                    "chattering_occurrences": chatter,
                    "stale_occurrences": stale,
                    "unacknowledged_occurrences": unacked,
                    "reasons": reasons,
                    "recommendations": rec,
                }
            )
        candidates.sort(key=lambda c: -c["occurrences"])
        return {"candidate_count": len(candidates), "candidates": candidates}

    def priority_score(self, alarm_id: str) -> dict[str, Any]:
        a = self.alarm(alarm_id)
        asset = ASSET_INDEX[a.asset_id]
        sev_pts = {"critical": 40, "high": 30, "medium": 18, "low": 8}[a.severity]
        crit_pts = {"A": 25, "B": 15, "C": 8}[asset.criticality]
        recent = self.filter(
            Scope(asset_ids=[a.asset_id], start=a.start_time - timedelta(days=30), end=a.start_time)
        )
        same = sum(1 for x in recent if x.alarm_code == a.alarm_code)
        rec_pts = min(15, same * 1.5)
        if a.status == "active":
            age_min = (self.end - a.start_time).total_seconds() / 60
            unack_pts = 10 if not a.acknowledged and age_min > 10 else (5 if not a.acknowledged else 0)
        else:
            unack_pts = 0
        safety_pts = (
            10 if a.alarm_type in {"safety", "electrical"} and a.severity in {"critical", "high"} else 0
        )
        factors: list[dict[str, Any]] = [
            {"factor": "severity", "value": a.severity, "points": sev_pts, "max_points": 40},
            {"factor": "asset_criticality", "value": asset.criticality, "points": crit_pts, "max_points": 25},
            {"factor": "recurrence_30d", "value": same, "points": round(rec_pts, 1), "max_points": 15},
            {
                "factor": "unacknowledged_active",
                "value": not a.acknowledged and a.status == "active",
                "points": unack_pts,
                "max_points": 10,
            },
            {"factor": "safety_or_electrical", "value": a.alarm_type, "points": safety_pts, "max_points": 10},
        ]
        score = min(100.0, sum(float(f["points"]) for f in factors))
        band = "urgent" if score >= 80 else "high" if score >= 60 else "medium" if score >= 40 else "low"
        response = {"urgent": "< 5 min", "high": "< 15 min", "medium": "< 60 min", "low": "next shift"}[band]
        top = sorted(factors, key=lambda f: -float(f["points"]))[:2]  # type: ignore[arg-type]
        return {
            "alarm_id": a.alarm_id,
            "alarm_name": a.alarm_name,
            "asset_id": a.asset_id,
            "asset_name": a.asset_name,
            "status": a.status,
            "priority_score": round(score, 1),
            "priority_band": band,
            "target_response_time": response,
            "factors": factors,
            "rationale": (
                f"{a.alarm_name} scored {round(score, 1)} ({band}); main drivers: "
                + ", ".join(f"{f['factor']}={f['value']}" for f in top)
                + "."
            ),
        }

    # ------------------------------------------------------------------ calculations / KPIs
    def generate_calculation(self, calculation_type: str, filters: dict[str, Any]) -> dict[str, Any]:
        if calculation_type not in KPI_DEFINITIONS:
            raise InvalidRequest(f"unsupported calculation_type; supported: {sorted(KPI_DEFINITIONS)}")
        calc_id = f"CALC-{uuid.uuid4().hex[:12]}"
        definition = KPI_DEFINITIONS[calculation_type]
        record = {
            "calculation_id": calc_id,
            "calculation_type": calculation_type,
            "filters": filters,
            "language": "declarative-kpi",
            "code": definition["formula"],
            "created_at": _iso(datetime.now(UTC)),
        }
        self._calculations[calc_id] = record
        return record

    def execute_calculation(self, calculation_id: str, filters: dict[str, Any] | None) -> dict[str, Any]:
        record = self._calculations.get(calculation_id)
        if record is None:
            raise NotFound("calculation", calculation_id)
        f = {**record["filters"], **(filters or {})}
        scope = Scope(
            site=f.get("site"),
            unit=f.get("unit"),
            asset_ids=f.get("asset_ids"),
            start=_parse_dt(f.get("start_time")) or self.start,
            end=_parse_dt(f.get("end_time")) or self.end,
        )
        rows = self.filter(scope)
        hours = max(1.0, ((scope.end or self.end) - (scope.start or self.start)).total_seconds() / 3600)
        ctype = record["calculation_type"]
        # NOTE: generated "code" is descriptive only - the simulator never evaluates it.
        if ctype == "alarm_flood_index":
            fa = self.flood_analysis(scope, 10, 10)
            value, unit, breakdown = (
                fa["percent_alarms_in_flood"],
                "% of alarms in flood",
                {"flood_count": fa["flood_count"], "total_alarms": fa["total_alarms"]},
            )
        elif ctype == "critical_alarm_density":
            crit = sum(1 for a in rows if a.severity == "critical")
            value, unit, breakdown = (
                round(crit / hours * 24, 3),
                "critical alarms per day",
                {"critical_alarms": crit, "hours": round(hours, 1)},
            )
        elif ctype == "operator_response_efficiency":
            acks = [a.ack_delay_seconds for a in rows if a.ack_delay_seconds is not None]
            within = sum(1 for d in acks if d <= 300)
            value = round(100 * within / len(acks), 2) if acks else 0.0
            unit = "% acknowledged within 5 min"
            breakdown = {
                "acknowledged": len(acks),
                "within_target": within,
                "median_ack_delay_seconds": round(statistics.median(acks), 1) if acks else None,
            }
        elif ctype == "nuisance_alarm_score":
            rc = self.rationalization_candidates(scope, 8, 180)
            nuisance = sum(c["occurrences"] for c in rc["candidates"] if "chattering" in c["reasons"])
            value = round(100 * nuisance / len(rows), 2) if rows else 0.0
            unit, breakdown = "% alarms from chattering sources", {"candidate_sources": rc["candidate_count"]}
        else:  # alarm_rate_per_hour
            value, unit, breakdown = round(len(rows) / hours, 3), "alarms per hour", {"alarms": len(rows)}
        return {
            "calculation_id": calculation_id,
            "calculation_type": ctype,
            "filters": f,
            "result": {"value": value, "unit": unit, "breakdown": breakdown},
            "benchmark": KPI_DEFINITIONS[ctype].get("benchmark"),
        }


def _slope(ys: list[int]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2
    my = statistics.fmean(ys)
    num = sum((i - mx) * (y - my) for i, y in enumerate(ys))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else 0.0


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


KPI_DEFINITIONS: dict[str, dict[str, Any]] = {
    "alarm_flood_index": {
        "name": "Alarm flood index",
        "description": "Share of alarms annunciated during flood periods (>=10 alarms in 10 minutes).",
        "formula": "100 * count(alarms in flood windows) / count(alarms)",
        "unit": "%",
        "benchmark": "< 1% of time in flood (ISA-18.2 / EEMUA 191 guidance)",
    },
    "critical_alarm_density": {
        "name": "Critical alarm density",
        "description": "Critical-severity alarms per day for the selected scope.",
        "formula": "count(alarms where severity = 'critical') / days",
        "unit": "alarms/day",
        "benchmark": "Critical priority should be < 5% of annunciated alarms",
    },
    "operator_response_efficiency": {
        "name": "Operator response efficiency",
        "description": "Share of acknowledged alarms acknowledged within 5 minutes.",
        "formula": "100 * count(ack_delay <= 300s) / count(acknowledged)",
        "unit": "%",
        "benchmark": ">= 90%",
    },
    "nuisance_alarm_score": {
        "name": "Nuisance alarm score",
        "description": "Share of alarms produced by chattering sources.",
        "formula": "100 * count(alarms from chattering sources) / count(alarms)",
        "unit": "%",
        "benchmark": "< 5%",
    },
    "alarm_rate_per_hour": {
        "name": "Average alarm rate",
        "description": "Annunciated alarms per hour.",
        "formula": "count(alarms) / hours",
        "unit": "alarms/hour",
        "benchmark": "<= 6 per operator per hour (very likely acceptable)",
    },
}
