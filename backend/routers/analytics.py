"""Analytics over daily_reports + attendance, aggregated by date/location/shift.

Surfaces the metrics the old Netlify dashboard tracked (Deployed Rate, SD card
utilization, Good Hours, Reporting Rate, Verified Attendance) but computed
from our Supabase data instead of a Google Sheet.
"""
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from datetime import datetime, date, timedelta
from typing import Optional, Literal

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from deps import require_current_role

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")

general_or_chief = require_current_role("general", "chief")


def _parse_date(s: str, label: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{label} must be YYYY-MM-DD, got {s!r}")


def _today() -> date:
    return datetime.now(IST).date()


def _as_float(v):
    return float(v) if v is not None else 0.0


def _rollup(rows) -> dict:
    """Compute KPIs from a list of rows with the aggregate columns below."""
    avail = sum(_as_float(r["sum_avail"])     for r in rows)
    dep   = sum(_as_float(r["sum_deployed"])  for r in rows)
    lost  = sum(_as_float(r["sum_lost"])      for r in rows)
    recov = sum(_as_float(r["sum_recovered"]) for r in rows)
    gh_a  = sum(_as_float(r["sum_gh_actual"]) for r in rows)
    gh_p  = sum(_as_float(r["sum_gh_proj"])   for r in rows)
    sd_u  = sum(_as_float(r["sum_sd_used"])   for r in rows)
    sd_l  = sum(_as_float(r["sum_sd_left"])   for r in rows)
    att   = sum(_as_float(r["att_count"])     for r in rows)
    ver   = sum(_as_float(r["verified_count"])for r in rows)
    subs  = sum(_as_float(r["reports"])       for r in rows)
    active = sum(_as_float(r["active_ops"])   for r in rows)
    return {
        "deployed_rate":       (dep / avail) if avail > 0 else None,
        "good_hours_actual":   gh_a,
        "good_hours_projected":gh_p,
        "good_hours_ratio":    (gh_a / gh_p) if gh_p > 0 else None,
        "sd_used":             sd_u,
        "sd_left":             sd_l,
        "sd_utilization":      (sd_u / (sd_u + sd_l)) if (sd_u + sd_l) > 0 else None,
        "devices_available":   avail,
        "devices_deployed":    dep,
        "devices_lost":        lost,
        "devices_recovered":   recov,
        "attendance_total":    int(att),
        "attendance_verified": int(ver),
        "verified_rate":       (ver / att) if att > 0 else None,
        "reports_submitted":   int(subs),
        "active_ops":          int(active),
        "reporting_rate":      (subs / active) if active > 0 else None,
    }


# Shared CTE for op aggregates per (group_key). Embeds the grouping column name.
def _build_sql(group_col: str, scope_join: str, scope_param_count: int) -> str:
    """
    group_col   : 'r.report_date', 'o.location', 'o.shift'
    scope_join  : '' for general, 'JOIN op_assignments ...' for chief
    scope_param_count : number of $N params consumed before the date range
    """
    # Params: [email?,] from_date, to_date
    p_from = f"${scope_param_count + 1}"
    p_to   = f"${scope_param_count + 2}"
    return f"""
        WITH op_day AS (
          SELECT
            o.op_id, o.location, o.shift, o.is_active,
            d::date AS report_date
          FROM operations o
          {scope_join}
          CROSS JOIN generate_series({p_from}::date, {p_to}::date, interval '1 day') d
        ),
        rpt AS (
          SELECT op_id, report_date,
                 1 AS reports,
                 COALESCE(devices_available,0)  AS avail,
                 COALESCE(devices_deployed,0)   AS deployed,
                 COALESCE(devices_lost,0)       AS lost,
                 COALESCE(devices_recovered,0)  AS recovered,
                 COALESCE(good_hours_actual,0)  AS gh_actual,
                 COALESCE(good_hours_projected,0) AS gh_proj,
                 COALESCE(sd_cards_used,0)      AS sd_used,
                 COALESCE(sd_cards_left,0)      AS sd_left
            FROM daily_reports
           WHERE report_date BETWEEN {p_from}::date AND {p_to}::date
        ),
        att AS (
          SELECT op_id, report_date,
                 COUNT(*) AS att_count,
                 COUNT(*) FILTER (WHERE verified) AS verified_count
            FROM attendance
           WHERE report_date BETWEEN {p_from}::date AND {p_to}::date
           GROUP BY op_id, report_date
        )
        SELECT
          {group_col} AS group_key,
          COUNT(*) FILTER (WHERE op_day.is_active) AS active_ops,
          COALESCE(SUM(r.reports),     0) AS reports,
          COALESCE(SUM(r.avail),       0) AS sum_avail,
          COALESCE(SUM(r.deployed),    0) AS sum_deployed,
          COALESCE(SUM(r.lost),        0) AS sum_lost,
          COALESCE(SUM(r.recovered),   0) AS sum_recovered,
          COALESCE(SUM(r.gh_actual),   0) AS sum_gh_actual,
          COALESCE(SUM(r.gh_proj),     0) AS sum_gh_proj,
          COALESCE(SUM(r.sd_used),     0) AS sum_sd_used,
          COALESCE(SUM(r.sd_left),     0) AS sum_sd_left,
          COALESCE(SUM(a.att_count),   0) AS att_count,
          COALESCE(SUM(a.verified_count), 0) AS verified_count
        FROM op_day
        LEFT JOIN rpt r USING (op_id, report_date)
        LEFT JOIN att a USING (op_id, report_date)
        GROUP BY {group_col}
        ORDER BY {group_col}
    """


@router.get("")
async def analytics(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to:    Optional[str] = Query(None),
    group: Literal["date", "location", "shift"] = "date",
    claims: dict = Depends(general_or_chief),
):
    # Default range: last 7 days ending today (IST).
    to_d   = _parse_date(to,    "to")   if to    else _today()
    from_d = _parse_date(from_, "from") if from_ else (to_d - timedelta(days=6))
    if from_d > to_d:
        raise HTTPException(400, "'from' must be <= 'to'")

    group_col = {
        "date":     "op_day.report_date",
        "location": "op_day.location",
        "shift":    "op_day.shift",
    }[group]

    email = (claims.get("email") or "").lower()
    if claims["role"] == "general":
        scope_join = ""
        params = [from_d, to_d]
    else:
        scope_join = "JOIN op_assignments asn ON asn.op_id = o.op_id AND asn.email = $1"
        params = [email, from_d, to_d]

    sql = _build_sql(group_col, scope_join, scope_param_count=len(params) - 2)
    db  = request.app.state.db
    rows = await db.fetch(sql, *params)

    out_rows = []
    for r in rows:
        gk = r["group_key"]
        if hasattr(gk, "isoformat"):
            gk = gk.isoformat()
        out_rows.append({
            "key": gk,
            "kpis": _rollup([r]),
        })

    return {
        "from":    from_d.isoformat(),
        "to":      to_d.isoformat(),
        "group":   group,
        "overall": _rollup(rows),
        "rows":    out_rows,
    }
