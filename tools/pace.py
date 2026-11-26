#!/usr/bin/env python3
"""Pace work against an AI plan allowance and a 200K-token task limit.

Claude desktop sessions should call mcp__ccd_session_mgmt__get_usage
(the tool also known as get_usage), then pass a window's percentUsed and
resetsAt into this script. Codex sessions can use --codex / --codex-context
to read the latest local session files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

UK_TZ_NAME = "Europe/London"
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_LOG = TOOLS_DIR / "usage-log.jsonl"
DEFAULT_CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

ZONE_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}
TOKEN_OK = "OK"
TOKEN_FINISH = "FINISH STEP, THEN HAND OVER"
TOKEN_NOW = "HANDOVER NOW"
MIN_ELAPSED_FOR_LINEAR = timedelta(minutes=10)

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def uk_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(UK_TZ_NAME)
        except Exception:
            pass
    return timezone(timedelta(hours=0), name="UTC")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=uk_tz())
    return dt.astimezone(timezone.utc)


def parse_time(value: Any) -> datetime:
    """Parse ISO, UK-local, or unix timestamps into an aware UTC datetime."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if re.fullmatch(r"\d{9,12}(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    iso = text
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    # "2026-11-28 01:00" or with seconds
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?", iso):
        iso = iso.replace(" ", "T")
        dt = datetime.fromisoformat(iso)
        return as_aware_utc(dt)
    try:
        dt = datetime.fromisoformat(iso)
        return as_aware_utc(dt)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M %Z", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return as_aware_utc(dt)
        except ValueError:
            continue
    raise ValueError("unrecognised time: %s" % text)


def format_uk(dt: datetime) -> str:
    local = dt.astimezone(uk_tz())
    return local.strftime("%Y-%m-%d %H:%M") + " UK"


def format_pct(n: float) -> str:
    if abs(n - round(n)) < 0.05:
        return str(int(round(n)))
    return "%.1f" % n


# ---------------------------------------------------------------------------
# Allowance zoning
# ---------------------------------------------------------------------------

def stricter(*zones: str) -> str:
    chosen = "GREEN"
    for z in zones:
        if z not in ZONE_RANK:
            raise ValueError("unknown zone: %s" % z)
        if ZONE_RANK[z] > ZONE_RANK[chosen]:
            chosen = z
    return chosen


def allowance_zone(used: float, projected: float) -> Tuple[str, List[str]]:
    """Return (zone, reasons). If rules disagree, the stricter zone wins.

    GREEN: projected under 85% by reset.
    AMBER: projected 85-99%, or under 25% left.
    RED: under 10% left, or projected to hit 100% before reset.
    A 5% reserve is kept for handover (used >= 95% is always RED).
    """
    remaining = 100.0 - used
    reasons: List[str] = []
    zones: List[str] = []

    if projected >= 100:
        zones.append("RED")
        reasons.append("projected to hit 100%% before reset (projected %s%%)" % format_pct(projected))
    elif projected >= 85:
        zones.append("AMBER")
        reasons.append("projected %s%% by reset (85-99%%)" % format_pct(projected))
    else:
        zones.append("GREEN")
        reasons.append("projected %s%% by reset (under 85%%)" % format_pct(projected))

    if remaining < 10:
        zones.append("RED")
        reasons.append("under 10%% left (remaining %s%%)" % format_pct(remaining))
    elif remaining < 25:
        zones.append("AMBER")
        reasons.append("under 25%% left (remaining %s%%)" % format_pct(remaining))

    if remaining <= 5 or used >= 95:
        zones.append("RED")
        reasons.append("5% handover reserve: do not spend below 5% left")

    zone = stricter(*zones) if zones else "GREEN"
    # Keep only reasons that support the winning zone, plus a note if we upgraded
    winning = []
    lower = []
    for z, r in zip(zones, reasons):
        if z == zone:
            winning.append(r)
        elif ZONE_RANK[z] < ZONE_RANK[zone]:
            lower.append("%s would have been %s" % (r, z))
    if lower:
        winning.append("stricter zone used; " + "; ".join(lower))
    return zone, winning


def window_start(resets_at: datetime, window_hours: float) -> datetime:
    return resets_at - timedelta(hours=window_hours)


def project_used(
    used: float,
    resets_at: datetime,
    window_hours: float,
    now: datetime,
    previous: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """Project percent used at reset.

    Prefer spend rate from the previous log reading in this window.
    With no history, assume linear spend from the window start.
    """
    remaining_hours = (resets_at - now).total_seconds() / 3600.0
    if remaining_hours < 0:
        remaining_hours = 0.0

    if previous is not None:
        try:
            prev_used = float(previous["used"])
            prev_ts = parse_time(previous["ts"])
        except (KeyError, TypeError, ValueError, ValueError):
            prev_used = None  # type: ignore
            prev_ts = None  # type: ignore
        else:
            dt_hours = (now - prev_ts).total_seconds() / 3600.0
            if dt_hours >= (2 / 60) and used >= prev_used:
                rate = (used - prev_used) / dt_hours
                projected = used + rate * remaining_hours
                return projected, "spend rate from last log reading"

    start = window_start(resets_at, window_hours)
    elapsed = now - start
    min_elapsed = max(MIN_ELAPSED_FOR_LINEAR, timedelta(hours=window_hours) * 0.01)
    if elapsed < min_elapsed:
        return used, "too early in the window for a reliable projection; using current use"
    elapsed_hours = elapsed.total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return used, "window has not started; using current use"
    rate = used / elapsed_hours
    return rate * window_hours, "linear spend from window start"


def log_path() -> Path:
    override = os.environ.get("PACE_LOG_PATH")
    return Path(override) if override else DEFAULT_LOG


def read_previous(
    log: Path,
    resets_at: datetime,
    window_hours: float,
    window_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not log.is_file():
        return None
    last = None
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") not in (None, "allowance"):
                    continue
                rec_key = rec.get("window_key") or rec.get("label")
                if window_key:
                    # Unlabelled older rows count only for the all-models window.
                    if rec_key and rec_key != window_key:
                        continue
                    if not rec_key and window_key not in (
                        "all-models",
                        "Weekly · all models",
                    ):
                        continue
                elif rec_key and rec_key not in ("all-models", "Weekly · all models"):
                    continue
                try:
                    rec_reset = parse_time(rec.get("resets_at"))
                    rec_window = float(rec.get("window_hours"))
                except (TypeError, ValueError):
                    continue
                if abs(rec_window - window_hours) > 0.01:
                    continue
                # Same window if reset times match within 2 minutes
                if abs((rec_reset - resets_at).total_seconds()) <= 120:
                    last = rec
    except OSError:
        return None
    return last


def append_log(log: Path, record: Dict[str, Any]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def run_allowance(
    used: float,
    resets_at_raw: str,
    window_hours: float,
    now: Optional[datetime] = None,
    write_log: bool = True,
    window_key: Optional[str] = None,
) -> str:
    try:
        reading = compute_allowance(
            used, resets_at_raw, window_hours, now=now, window_key=window_key
        )
    except ValueError as e:
        raise SystemExit("error: %s" % e)
    if write_log and not reading.get("stale"):
        now_val = reading["now"]
        append_log(
            log_path(),
            {
                "kind": "allowance",
                "ts": now_val.astimezone(timezone.utc).isoformat(),
                "used": reading["used"],
                "projected": round(float(reading["projected"]), 3),
                "resets_at": reading["resets_at"].astimezone(timezone.utc).isoformat(),
                "window_hours": reading["window_hours"],
                "zone": reading["zone"],
                "how": reading["how"],
                "window_key": window_key,
            },
        )
    return reading["line"]


def token_zone(n: int) -> str:
    if n < 160_000:
        return TOKEN_OK
    if n < 200_000:
        return TOKEN_FINISH
    return TOKEN_NOW


def run_tokens(n: int, worker: Optional[str] = None, write_log: bool = False) -> str:
    if n < 0:
        raise SystemExit("error: --tokens must be 0 or more, got %s" % n)
    zone = token_zone(n)
    if worker and write_log:
        append_log(
            log_path(),
            {
                "kind": "worker",
                "ts": now_utc().astimezone(timezone.utc).isoformat(),
                "worker": worker,
                "tokens": n,
                "zone": zone,
            },
        )
    return "TOKENS  %s  %s" % (n, zone)


# ---------------------------------------------------------------------------
# Codex local session reads
# ---------------------------------------------------------------------------

def sessions_dir() -> Path:
    override = os.environ.get("PACE_CODEX_SESSIONS")
    return Path(override) if override else DEFAULT_CODEX_SESSIONS


def iter_session_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if name.endswith(".jsonl") or name.endswith(".jsonl.zst"):
                files.append(p)
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files


def read_tail_text(path: Path, max_bytes: int = 4_000_000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
                data = f.read()
                # drop a possible partial first line
                nl = data.find(b"\n")
                if nl != -1:
                    data = data[nl + 1 :]
            else:
                data = f.read()
        return data.decode("utf-8", "replace")
    except OSError:
        return ""


def extract_rate_limits(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        rl = obj.get("rate_limits") or obj.get("rateLimits")
        if isinstance(rl, dict) and ("primary" in rl or "used_percent" in rl):
            return rl
        payload = obj.get("payload")
        if isinstance(payload, dict):
            found = extract_rate_limits(payload)
            if found:
                return found
    return None


def extract_token_count(obj: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(obj, dict):
        return None
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
    if payload.get("type") == "token_count" and isinstance(payload.get("info"), dict):
        return payload["info"]
    # some records nest msg
    msg = payload.get("msg") if isinstance(payload.get("msg"), dict) else None
    if msg and msg.get("type") == "token_count" and isinstance(msg.get("info"), dict):
        return msg["info"]
    return None


def record_timestamp(obj: Dict[str, Any]) -> Optional[datetime]:
    for key in ("timestamp", "ts"):
        raw = obj.get(key)
        if raw:
            try:
                return parse_time(raw)
            except ValueError:
                pass
    payload = obj.get("payload")
    if isinstance(payload, dict) and payload.get("timestamp"):
        try:
            return parse_time(payload["timestamp"])
        except ValueError:
            pass
    return None


def _event_sort_key(item: Optional[Dict[str, Any]], path: Path) -> Tuple[int, float]:
    """Prefer a later event timestamp; fall back to the file's mtime."""
    if not item:
        return (0, 0.0)
    at = item.get("at")
    if isinstance(at, datetime):
        return (1, at.timestamp())
    try:
        return (0, path.stat().st_mtime)
    except OSError:
        return (0, 0.0)


def scan_codex_sessions(root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (latest_rate, latest_tokens) from session files.

    Account allowance and token counts are taken from the event with the
    latest timestamp, not merely the file most recently touched. A large
    older session can be touched on this Mac without being the newest turn.
    Each item is a dict with keys: data, at, file.
    """
    latest_rate = None
    latest_tokens = None
    latest_rate_key = (0, 0.0)
    latest_tokens_key = (0, 0.0)
    for path in iter_session_files(root)[:40]:
        if path.name.endswith(".zst"):
            continue  # uncompressed jsonl is enough on this Mac
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        text = read_tail_text(path, 4_000_000 if size > 8_000_000 else size)
        lines = text.splitlines()
        found_rate = None
        found_tokens = None
        found_rate_at = None
        found_tokens_at = None
        for line in reversed(lines):
            if not line.strip():
                continue
            if "rate_limits" not in line and "rateLimits" not in line and "token_count" not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = record_timestamp(rec) if isinstance(rec, dict) else None
            if found_rate is None:
                rl = extract_rate_limits(rec)
                if rl is not None:
                    found_rate = rl
                    found_rate_at = at
            if found_tokens is None:
                info = extract_token_count(rec)
                if info is not None:
                    found_tokens = info
                    found_tokens_at = at
            if found_rate is not None and found_tokens is not None:
                break
        if found_rate is not None:
            item = {"data": found_rate, "at": found_rate_at, "file": str(path)}
            key = _event_sort_key(item, path)
            if key >= latest_rate_key:
                latest_rate, latest_rate_key = item, key
        if found_tokens is not None:
            item = {"data": found_tokens, "at": found_tokens_at, "file": str(path)}
            key = _event_sort_key(item, path)
            if key >= latest_tokens_key:
                latest_tokens, latest_tokens_key = item, key
    return latest_rate, latest_tokens


def describe_codex_window(name: str, window: Optional[Dict[str, Any]], now: datetime) -> Tuple[Optional[str], Optional[str]]:
    """Return (zone_line or None, status_snippet)."""
    if not isinstance(window, dict):
        return None, None
    used = window.get("used_percent")
    resets_raw = window.get("resets_at")
    minutes = window.get("window_minutes")
    if used is None or resets_raw is None:
        return None, "%s: incomplete" % name
    try:
        used_f = float(used)
        resets_at = parse_time(resets_raw)
    except (TypeError, ValueError):
        return None, "%s: unreadable" % name
    hours = float(minutes) / 60.0 if minutes else 168.0
    if now >= resets_at:
        snippet = (
            "%s STALE used=%s%% resets-at=%s window=%sh (reset has passed; do one turn, then read again)"
            % (name, format_pct(used_f), format_uk(resets_at), format_pct(hours))
        )
        return "STALE", snippet
    # Reuse allowance engine (also logs)
    line = run_allowance(used_f, resets_at.isoformat(), hours, now=now, write_log=True)
    return line.split()[0], "%s %s" % (name, line)


def run_codex(now: Optional[datetime] = None) -> str:
    now = now or now_utc()
    root = sessions_dir()
    if not root.is_dir():
        return (
            "UNAVAILABLE  no Codex session folder at %s. "
            "Allowance is only written during a live Codex turn."
            % root
        )
    latest_rate, _ = scan_codex_sessions(root)
    if latest_rate is None:
        return (
            "UNAVAILABLE  no rate-limit data found in %s. "
            "Codex writes this during a live session; start a Codex turn, then try again."
            % root
        )
    rl = latest_rate["data"]
    as_of = latest_rate["at"]
    as_of_s = format_uk(as_of) if as_of else "unknown time"
    parts = ["CODEX allowance  as of %s" % as_of_s]
    zones: List[str] = []
    for name, key in (("primary", "primary"), ("secondary", "secondary")):
        window = rl.get(key)
        if window is None and key == "secondary":
            continue
        zone, snippet = describe_codex_window(name, window, now)
        if snippet:
            parts.append(snippet)
        if zone:
            zones.append(zone)
    plan = rl.get("plan_type")
    if plan:
        parts.append("plan=%s" % plan)
    # Overall: STALE if every window is stale; else stricter of computed zones
    if zones and all(z == "STALE" for z in zones):
        overall = "STALE"
    else:
        real = [z for z in zones if z in ZONE_RANK]
        overall = stricter(*real) if real else "UNAVAILABLE"
    return overall + "  " + "  |  ".join(parts)


def run_codex_context() -> str:
    root = sessions_dir()
    if not root.is_dir():
        return (
            "UNAVAILABLE  no Codex session folder at %s. "
            "Token counts are only written during a live Codex turn."
            % root
        )
    _, latest_tokens = scan_codex_sessions(root)
    if latest_tokens is None:
        return (
            "UNAVAILABLE  no token-count data found in %s. "
            "Codex writes this during a live session; start a Codex turn, then try again."
            % root
        )
    info = latest_tokens["data"]
    as_of = latest_tokens["at"]
    as_of_s = format_uk(as_of) if as_of else "unknown time"
    last = info.get("last_token_usage") or {}
    # Current context fill is the last request's input tokens, not the
    # cumulative billed total (which can exceed the model window).
    tokens = last.get("input_tokens")
    if tokens is None:
        return (
            "UNAVAILABLE  latest Codex token record (as of %s) has no last_token_usage.input_tokens"
            % as_of_s
        )
    try:
        n = int(tokens)
    except (TypeError, ValueError):
        return "UNAVAILABLE  could not read last_token_usage.input_tokens"
    zone_line = run_tokens(n)
    window = info.get("model_context_window")
    extra = "  as of %s" % as_of_s
    if window:
        extra += "  model-window=%s" % window
    return "CODEX context  %s%s" % (zone_line, extra)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Model choice, get_usage JSON, and relative-cost estimates
# ---------------------------------------------------------------------------

DEFAULT_COSTS_PATH = TOOLS_DIR / "model-costs.json"

# Baked-in defaults so pacing still works if the JSON file is missing.
# Relative to sonnet = 1.0, from published Claude API list prices.
DEFAULT_COSTS = {
    "default_model": "sonnet",
    "models": {
        "haiku": {
            "cost": 0.05,
            "aliases": [
                "haiku 5.5",
                "haiku 4.5",
                "claude-haiku-5-5",
                "claude-haiku-4-5",
                "claude-haiku",
                "claude-haiku-latest",
            ],
        },
        "sonnet": {
            "cost": 1.0,
            "aliases": [
                "sonnet 5.5",
                "sonnet 5",
                "claude-sonnet-5-5",
                "claude-sonnet-5",
                "claude-sonnet",
                "claude-sonnet-latest",
            ],
        },
        "opus": {
            "cost": 2.0,
            "aliases": [
                "opus 5.5",
                "opus 5",
                "opus 4.8",
                "claude-opus-5-5",
                "claude-opus-5",
                "claude-opus-4-8",
                "claude-opus",
                "claude-opus-latest",
            ],
        },
        "fable": {
            "cost": 4.0,
            "aliases": [
                "fable 5.2",
                "fable 5.1",
                "fable 5",
                "claude-fable-5-2",
                "claude-fable-5-1",
                "claude-fable-5",
                "claude-fable",
                "claude-fable-latest",
            ],
        },
    },
}


def costs_path() -> Path:
    override = os.environ.get("PACE_COSTS_PATH")
    return Path(override) if override else DEFAULT_COSTS_PATH


def _deep_merge_models(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for name, info in overlay.items():
        key = str(name).strip().lower()
        if not isinstance(info, dict):
            continue
        current = dict(out.get(key) or {})
        if "cost" in info:
            current["cost"] = float(info["cost"])
        aliases = list(current.get("aliases") or [])
        for a in info.get("aliases") or []:
            if a not in aliases:
                aliases.append(a)
        current["aliases"] = aliases
        out[key] = current
    return out


def load_costs() -> Dict[str, Any]:
    data = {
        "default_model": DEFAULT_COSTS["default_model"],
        "models": {k: dict(v, aliases=list(v.get("aliases") or [])) for k, v in DEFAULT_COSTS["models"].items()},
    }
    path = costs_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict):
            if raw.get("default_model"):
                data["default_model"] = str(raw["default_model"]).strip().lower()
            if isinstance(raw.get("models"), dict):
                data["models"] = _deep_merge_models(data["models"], raw["models"])
    return data


def normalize_model_name(name: str) -> str:
    text = str(name).strip().lower()
    text = text.replace("claude-", "")
    text = text.replace("_", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def family_key(name: str) -> str:
    text = normalize_model_name(name)
    # "opus 5.5", "fable-5-2", "Weekly · Fable" -> first token
    text = text.replace("·", " ").replace("•", " ")
    text = re.sub(r"[^a-z0-9.\s-]+", " ", text)
    parts = [p for p in re.split(r"[\s-]+", text) if p and p not in {"weekly", "all", "models", "limit", "hour", "5"}]
    return parts[0] if parts else text


def resolve_model(name: str, costs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve a user-facing model name to a costs-table entry."""
    costs = costs or load_costs()
    models = costs.get("models") or {}
    raw = str(name).strip()
    norm = normalize_model_name(raw)
    fam = family_key(raw)

    def aliases_of(key: str, info: Dict[str, Any]) -> List[str]:
        found = [key, normalize_model_name(key), family_key(key)]
        for a in info.get("aliases") or []:
            found.extend([str(a), normalize_model_name(a), family_key(a)])
        return found

    # Exact key or exact alias first, then family match.
    for key, info in models.items():
        names = [normalize_model_name(x) for x in aliases_of(key, info)]
        if norm in names:
            return {"id": key, "cost": float(info.get("cost", 1.0)), "input": raw, "known": True}
    for key, info in models.items():
        names = [family_key(x) for x in aliases_of(key, info)]
        if fam in names:
            return {"id": key, "cost": float(info.get("cost", 1.0)), "input": raw, "known": True}
    return {"id": fam or norm, "cost": None, "input": raw, "known": False}


def infer_window_hours(label: str, explicit: Optional[float] = None) -> float:
    if explicit is not None:
        return float(explicit)
    lab = (label or "").lower()
    if "5-hour" in lab or "5 hour" in lab:
        return 5.0
    return 168.0


def classify_window(label: str) -> Tuple[str, Optional[str]]:
    """Return (kind, family) where kind is all-models, 5-hour, or per-model."""
    lab = (label or "").strip()
    low = lab.lower()
    if "all models" in low:
        return "all-models", None
    if "5-hour" in low or "5 hour" in low:
        return "5-hour", None
    m = re.search(r"(?:weekly|daily)\s*[·•\-|:]\s*(.+)$", lab, re.I)
    if m:
        return "per-model", family_key(m.group(1))
    # Bare family names such as "Fable"
    fam = family_key(lab)
    if fam and fam not in {"weekly", "limit"}:
        return "per-model", fam
    return "other", None


def extract_usage_windows(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [w for w in data if isinstance(w, dict)]
    if not isinstance(data, dict):
        raise ValueError("usage JSON must be an object or a list of windows")
    if isinstance(data.get("windows"), list):
        return [w for w in data["windows"] if isinstance(w, dict)]
    plan = data.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("windows"), list):
        return [w for w in plan["windows"] if isinstance(w, dict)]
    for key in ("result", "content", "data"):
        if key in data:
            try:
                return extract_usage_windows(data[key])
            except ValueError:
                continue
    raise ValueError("no plan.windows found in usage JSON")


def load_usage_json(source: str) -> List[Dict[str, Any]]:
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError("cannot read %s: %s" % (source, e))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("invalid usage JSON: %s" % e)
    return extract_usage_windows(data)


def compute_allowance(
    used: float,
    resets_at_raw: str,
    window_hours: float,
    now: Optional[datetime] = None,
    window_key: Optional[str] = None,
) -> Dict[str, Any]:
    if used < 0 or used > 100:
        raise ValueError("--used must be a percent between 0 and 100, got %s" % used)
    if window_hours <= 0:
        raise ValueError("--window-hours must be positive, got %s" % window_hours)
    now = now or now_utc()
    resets_at = parse_time(resets_at_raw)
    remaining = 100.0 - used
    if now >= resets_at:
        line = (
            "STALE  used=%s%%  resets-at=%s  window=%sh  "
            "the reset has already passed; do one turn, then read the meter again"
            % (format_pct(used), format_uk(resets_at), format_pct(float(window_hours)))
        )
        return {
            "zone": "STALE",
            "used": used,
            "projected": used,
            "remaining": remaining,
            "resets_at": resets_at,
            "window_hours": float(window_hours),
            "how": "stale",
            "reasons": [],
            "stale": True,
            "line": line,
            "window_key": window_key,
            "now": now,
        }
    previous = read_previous(log_path(), resets_at, window_hours, window_key=window_key)
    projected, how = project_used(used, resets_at, window_hours, now, previous)
    if projected < used:
        projected = used
    zone, reasons = allowance_zone(used, projected)
    line = (
        "%s  used=%s%%  projected=%s%%  remaining=%s%%  resets-at=%s  "
        "window=%sh  %s"
        % (
            zone,
            format_pct(used),
            format_pct(projected),
            format_pct(remaining),
            format_uk(resets_at),
            format_pct(float(window_hours)),
            how,
        )
    )
    if reasons:
        line += "  | " + "; ".join(reasons)
    if zone == "RED":
        line += "  | Keep a 5% reserve for handover."
    return {
        "zone": zone,
        "used": used,
        "projected": projected,
        "remaining": remaining,
        "resets_at": resets_at,
        "window_hours": float(window_hours),
        "how": how,
        "reasons": reasons,
        "stale": False,
        "line": line,
        "window_key": window_key,
        "now": now,
    }


def pick_tighter(readings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not readings:
        return None
    active = [r for r in readings if not r.get("stale")]
    pool = active or readings

    def rank(r: Dict[str, Any]) -> Tuple[int, float, float]:
        return (ZONE_RANK.get(r.get("zone") or "", 0), float(r.get("used") or 0), float(r.get("projected") or 0))

    return max(pool, key=rank)


def reading_from_window(
    window: Dict[str, Any],
    kind: str,
    now: datetime,
    window_hours: Optional[float] = None,
) -> Dict[str, Any]:
    label = str(window.get("label") or kind)
    used = window.get("percentUsed")
    if used is None:
        used = window.get("used")
    resets = window.get("resetsAt") or window.get("resets_at")
    if used is None or resets is None:
        raise ValueError("window %r needs percentUsed and resetsAt" % label)
    hours = infer_window_hours(label, window_hours)
    reading = compute_allowance(float(used), resets, hours, now=now, window_key=label)
    reading["kind"] = kind
    reading["label"] = label
    return reading


def format_reading_line(reading: Dict[str, Any], heading: Optional[str] = None) -> str:
    title = heading if heading is not None else reading.get("label") or reading.get("kind") or "window"
    return "  %s  %s" % (title, reading["line"])


def apply_job_to_reading(reading: Dict[str, Any], job_pct: float) -> Dict[str, Any]:
    """Add an immediate job spend and re-zone, keeping the same reset window."""
    if reading.get("stale"):
        updated = dict(reading)
        updated["job_pct"] = job_pct
        updated["new_used"] = reading["used"]
        updated["new_projected"] = reading.get("projected", reading["used"])
        updated["fit_zone"] = "STALE"
        return updated
    new_used = min(reading["used"] + job_pct, 999.0)
    new_projected = min(max(reading["projected"] + job_pct, new_used), 999.0)
    if new_used > 100:
        zone, reasons = "RED", ["job would push used above 100%"]
    else:
        zone, reasons = allowance_zone(min(new_used, 100.0), new_projected)
    updated = dict(reading)
    updated["job_pct"] = job_pct
    updated["new_used"] = new_used
    updated["new_projected"] = new_projected
    updated["new_remaining"] = 100.0 - min(new_used, 100.0)
    updated["fit_zone"] = zone
    updated["fit_reasons"] = reasons
    return updated


def fit_status(updated_readings: List[Dict[str, Any]]) -> str:
    """DOES NOT FIT, TIGHT, or FITS across the windows a job would hit."""
    if any(r.get("stale") for r in updated_readings):
        return "STALE"
    for r in updated_readings:
        new_used = float(r.get("new_used", r["used"]))
        new_projected = float(r.get("new_projected", r.get("projected", new_used)))
        if new_used >= 95 or new_projected >= 100:
            return "DOES NOT FIT"
    zones = [r.get("fit_zone") for r in updated_readings]
    if any(z == "RED" or z == "AMBER" for z in zones):
        return "TIGHT"
    return "FITS"


def estimate_job(
    chosen: Dict[str, Any],
    estimate_pct: float,
    readings: List[Dict[str, Any]],
    costs: Dict[str, Any],
) -> List[str]:
    if estimate_pct < 0:
        raise ValueError("--estimate must be 0 or more, got %s" % estimate_pct)
    default_id = costs.get("default_model") or "sonnet"
    default = resolve_model(default_id, costs)
    default_cost = float(default["cost"] if default.get("cost") is not None else 1.0)
    if not chosen.get("known") or chosen.get("cost") is None:
        return [
            "ESTIMATE  UNAVAILABLE  no relative cost for model %s in ~/.claude/tools/model-costs.json"
            % chosen["input"]
        ]
    job_pct = estimate_pct * (float(chosen["cost"]) / default_cost)

    def affected(r: Dict[str, Any]) -> bool:
        if r.get("kind") in ("all-models", "5-hour"):
            return True
        if r.get("kind") == "per-model":
            return family_key(r.get("family") or r.get("label") or "") == chosen["id"]
        return False

    targets = [r for r in readings if affected(r)]
    if not targets:
        targets = [r for r in readings if r.get("kind") == "all-models"]
    updated = [apply_job_to_reading(r, job_pct) for r in targets]
    status = fit_status(updated)
    tighter = pick_tighter(
        [
            {
                **u,
                "zone": u.get("fit_zone") or u["zone"],
                "used": u.get("new_used", u["used"]),
                "projected": u.get("new_projected", u.get("projected", u["used"])),
                "stale": u.get("stale"),
            }
            for u in updated
        ]
    )
    focus = tighter or (updated[0] if updated else None)
    lines = [
        "ESTIMATE  job=%s%% on %s  →  %s%% on %s  new-used=%s%% on %s  %s"
        % (
            format_pct(estimate_pct),
            default["id"],
            format_pct(job_pct),
            chosen["id"],
            format_pct(focus["new_used"]) if focus else "?",
            (focus.get("label") or focus.get("kind") or "window") if focus else "window",
            status,
        )
    ]
    if status == "DOES NOT FIT":
        lines[-1] += "  | would hit the cap or the 5% handover reserve before reset"
    elif status == "TIGHT":
        lines[-1] += "  | fits, but would leave the tighter window AMBER or RED"

    # Suggest cheaper models for routine parts when the chosen model is
    # dearer than the default, or when the job is tight / does not fit.
    cheaper = []
    for key, info in (costs.get("models") or {}).items():
        cost = info.get("cost")
        if cost is None:
            continue
        if float(cost) < float(chosen["cost"]) - 1e-9:
            cheaper.append((float(cost), key, info))
    cheaper.sort()
    suggestions = []
    if cheaper and (float(chosen["cost"]) > default_cost + 1e-9 or status in ("TIGHT", "DOES NOT FIT", "STALE")):
        for cost, key, info in cheaper:
            alt = {"id": key, "cost": float(cost), "input": key, "known": True}
            alt_job = estimate_pct * (float(cost) / default_cost)
            alt_updated = [apply_job_to_reading(r, alt_job) for r in targets]
            # Per-model window of the chosen model does not apply to a cheaper other family
            alt_targets = []
            for r, u in zip(targets, alt_updated):
                if r.get("kind") == "per-model" and family_key(r.get("family") or r.get("label") or "") != key:
                    continue
                alt_targets.append(u)
            if not alt_targets:
                alt_targets = [u for u, r in zip(alt_updated, targets) if r.get("kind") in ("all-models", "5-hour")]
            alt_status = fit_status(alt_targets) if alt_targets else "FITS"
            alt_focus = pick_tighter(
                [
                    {
                        **u,
                        "zone": u.get("fit_zone") or u["zone"],
                        "used": u.get("new_used", u["used"]),
                        "projected": u.get("new_projected", u.get("projected", u["used"])),
                        "stale": u.get("stale"),
                    }
                    for u in alt_targets
                ]
            )
            new_used_s = format_pct(alt_focus["new_used"]) if alt_focus else "?"
            suggestions.append(
                "%s (job=%s%% %s new-used=%s%%)"
                % (key, format_pct(alt_job), alt_status, new_used_s)
            )
    if suggestions:
        lines.append("SUGGEST  for routine parts: " + "; ".join(suggestions))
    return lines


def collect_model_readings(
    model_name: Optional[str],
    usage_windows: Optional[List[Dict[str, Any]]],
    all_used: Optional[float],
    all_resets: Optional[str],
    all_hours: Optional[float],
    model_used: Optional[float],
    model_resets: Optional[str],
    model_hours: Optional[float],
    now: Optional[datetime] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    now = now or now_utc()
    costs = load_costs()
    chosen = resolve_model(model_name, costs) if model_name else None
    notes: List[str] = []
    readings: List[Dict[str, Any]] = []

    windows = list(usage_windows or [])
    if all_used is not None and all_resets:
        windows.append(
            {
                "label": "Weekly · all models",
                "percentUsed": all_used,
                "resetsAt": all_resets,
                "_hours": all_hours if all_hours is not None else 168.0,
                "_kind": "all-models",
            }
        )
    if model_used is not None and model_resets:
        label = "Weekly · %s" % ((chosen["id"].title()) if chosen else "model")
        windows.append(
            {
                "label": label,
                "percentUsed": model_used,
                "resetsAt": model_resets,
                "_hours": model_hours if model_hours is not None else 168.0,
                "_kind": "per-model",
                "_family": chosen["id"] if chosen else family_key(label),
            }
        )

    seen = set()
    for w in windows:
        label = str(w.get("label") or "")
        kind, family = classify_window(label)
        if w.get("_kind"):
            kind = w["_kind"]
        if w.get("_family"):
            family = w["_family"]
        hours = w.get("_hours")
        key = (kind, label)
        if key in seen:
            continue
        seen.add(key)
        reading = reading_from_window(w, kind, now, window_hours=hours)
        reading["family"] = family
        readings.append(reading)

    # If a model was named, note a missing per-model window (e.g. no Opus window).
    if chosen:
        matched = [
            r
            for r in readings
            if r.get("kind") == "per-model" and (r.get("family") or family_key(r.get("label") or "")) == chosen["id"]
        ]
        if not matched:
            expected = "Weekly · %s" % chosen["id"].title()
            notes.append(
                '  per-model  none  no "%s" window in this meter (only all-models applies for %s)'
                % (expected, chosen["id"])
            )
    return chosen, readings, notes


def run_model_pace(
    model_name: Optional[str] = None,
    usage_windows: Optional[List[Dict[str, Any]]] = None,
    all_used: Optional[float] = None,
    all_resets: Optional[str] = None,
    all_hours: Optional[float] = None,
    model_used: Optional[float] = None,
    model_resets: Optional[str] = None,
    model_hours: Optional[float] = None,
    estimate_pct: Optional[float] = None,
    now: Optional[datetime] = None,
    write_log: bool = True,
    sessions: Optional[int] = None,
) -> str:
    if sessions is not None and sessions < 1:
        raise ValueError("--sessions must be 1 or more, got %s" % sessions)
    now = now or now_utc()
    costs = load_costs()
    chosen, readings, notes = collect_model_readings(
        model_name,
        usage_windows,
        all_used,
        all_resets,
        all_hours,
        model_used,
        model_resets,
        model_hours,
        now=now,
    )
    if not readings:
        raise ValueError(
            "model mode needs --usage-json, or --used/--resets-at for the all-models window"
        )

    def applies_to_chosen(r: Dict[str, Any]) -> bool:
        if r.get("kind") in ("all-models", "5-hour"):
            return True
        if not chosen:
            return True
        if r.get("kind") == "per-model":
            return (r.get("family") or family_key(r.get("label") or "")) == chosen["id"]
        return False

    tighter = pick_tighter([r for r in readings if applies_to_chosen(r)]) or pick_tighter(readings)
    # Headline
    if chosen:
        head = "MODEL %s" % chosen["id"]
        if chosen["input"] and normalize_model_name(chosen["input"]) not in (
            chosen["id"],
            normalize_model_name(chosen["id"]),
        ):
            head += " (%s)" % chosen["input"]
    else:
        head = "WINDOWS"
    if tighter:
        kind = tighter.get("kind") or tighter.get("label") or "window"
        line = (
            "%s  tighter=%s:%s  used=%s%%  projected=%s%%  remaining=%s%%  "
            "resets-at=%s  window=%sh"
            % (
                head,
                kind,
                tighter["zone"],
                format_pct(tighter["used"]),
                format_pct(tighter["projected"]),
                format_pct(tighter["remaining"]),
                format_uk(tighter["resets_at"]),
                format_pct(tighter["window_hours"]),
            )
        )
    else:
        line = head + "  UNAVAILABLE  no readable windows"
    lines = [line]

    # Stable order: all-models, 5-hour, per-model (matching first), other notes
    order = {"all-models": 0, "5-hour": 1, "per-model": 2}
    for r in sorted(readings, key=lambda x: (order.get(x.get("kind") or "", 9), x.get("label") or "")):
        heading = r.get("kind") if r.get("kind") in ("all-models", "5-hour") else r.get("label")
        lines.append(format_reading_line(r, heading=heading))
    lines.extend(notes)

    if estimate_pct is not None:
        if not chosen:
            chosen = resolve_model(costs.get("default_model") or "sonnet", costs)
            lines.append("ESTIMATE  using default model %s (pass --model to choose)" % chosen["id"])
        total_pct = float(estimate_pct) * (sessions or 1)
        if sessions and sessions > 1:
            lines.append(
                "PARALLEL  %d workers x %s%% = %s%% total on %s (all share one allowance)"
                % (sessions, format_pct(float(estimate_pct)), format_pct(total_pct), costs.get("default_model") or "sonnet")
            )
        lines.extend(estimate_job(chosen, total_pct, readings, costs))

    if sessions and tighter:
        spendable = max(0.0, float(tighter["remaining"]) - 5.0)
        lines.append(
            "SHARE  %d workers  %s%% spendable (%s%% left minus the 5%% reserve)  =  %s%% each, on the %s window"
            % (
                sessions,
                format_pct(spendable),
                format_pct(tighter["remaining"]),
                format_pct(spendable / sessions),
                tighter.get("label") or tighter.get("kind") or "tighter",
            )
        )

    if write_log and tighter and not tighter.get("stale"):
        append_log(
            log_path(),
            {
                "kind": "allowance",
                "ts": now.astimezone(timezone.utc).isoformat(),
                "used": tighter["used"],
                "projected": round(float(tighter["projected"]), 3),
                "resets_at": tighter["resets_at"].astimezone(timezone.utc).isoformat(),
                "window_hours": tighter["window_hours"],
                "zone": tighter["zone"],
                "how": tighter.get("how"),
                "window_key": tighter.get("label") or tighter.get("kind"),
                "model": chosen["id"] if chosen else None,
                **({"sessions": sessions} if sessions else {}),
            },
        )
    return "\n".join(lines)

HELP = """\
Usage:
  python3 %(prog)s --used <percent> --resets-at <time> --window-hours <n>
  python3 %(prog)s --tokens <n>
  python3 %(prog)s --codex
  python3 %(prog)s --codex-context
  python3 %(prog)s --model <name> --usage-json <file|->
  python3 %(prog)s --model <name> --used <pct> --resets-at <time> --window-hours <n> \\
      [--model-used <pct> --model-resets-at <time> --model-window-hours <n>]
  python3 %(prog)s --model <name> --usage-json <file|-> --estimate <pct-on-default-model>
  python3 %(prog)s --model <name> --usage-json <file|-> --estimate <pct> --sessions <N>
  python3 %(prog)s --tokens <n> --worker <job-name>
  python3 %(prog)s --self-test
  python3 %(prog)s --help

Allowance zones:
  GREEN  projected under 85%% by reset
  AMBER  projected 85-99%%, or under 25%% left
  RED    under 10%% left, or projected to hit 100%% before reset
  If two rules disagree, the stricter zone is used.
  Keep a 5%% reserve for handover (never spend below 5%% left).
  STALE  means the reset time has already passed: do one turn, then read again.

Task tokens:
  OK                              under 160000
  FINISH STEP, THEN HAND OVER     160000-199999
  HANDOVER NOW                    200000 or more

Claude desktop: call mcp__ccd_session_mgmt__get_usage (also called get_usage).
  Save the JSON and pass it with --usage-json, or copy plan.windows[].percentUsed
  and resetsAt into --used and --resets-at (all-models) and --model-used and
  --model-resets-at (the matching per-model window).
  context.tokensUsed goes to --tokens.
  Use --window-hours 168 for a weekly window, 5 for a 5-hour window.

Model choice:
  --model picks a family (opus, sonnet, haiku, fable, or a name like "Opus 5.5").
  The script reports the all-models window and the matching per-model window
  (for example "Weekly · Fable"), then the tighter of the two.
  --estimate <pct> is the job's size as a percent of the default model (sonnet
  unless you change ~/.claude/tools/model-costs.json). It projects spend on the
  chosen model, says FITS, TIGHT, or DOES NOT FIT, and may suggest a cheaper
  model for routine parts.
  --sessions <N> says N workers share the one allowance. --estimate is then each
  worker's size and the check runs on the total; a SHARE line says how much of the
  allowance (minus the 5%% reserve) each worker may spend.
  --tokens <n> --worker <name> also writes one log line for that worker.

Codex: --codex reads the latest rate_limits from ~/.codex/sessions.
  --codex-context reads the latest last_token_usage.input_tokens.
  Both say UNAVAILABLE if no local session data is present.

This script lives at ~/.claude/tools/pace.py
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pace.py",
        add_help=False,
        description="Pace work against usage and the 200K-token task limit.",
    )
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--used", type=float)
    p.add_argument("--resets-at", dest="resets_at")
    p.add_argument("--window-hours", dest="window_hours", type=float)
    p.add_argument("--tokens", type=int)
    p.add_argument("--codex", action="store_true")
    p.add_argument("--codex-context", dest="codex_context", action="store_true")
    p.add_argument("--model")
    p.add_argument("--usage-json", dest="usage_json")
    p.add_argument("--model-used", dest="model_used", type=float)
    p.add_argument("--model-resets-at", dest="model_resets_at")
    p.add_argument("--model-window-hours", dest="model_window_hours", type=float)
    p.add_argument("--estimate", type=float)
    p.add_argument("--sessions", type=int)
    p.add_argument("--worker")
    p.add_argument("--self-test", dest="self_test", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    if args.help or not argv:
        sys.stdout.write(HELP % {"prog": "pace.py"})
        return 0
    if args.self_test:
        return run_self_tests()

    model_mode = any(
        [
            args.model,
            args.usage_json,
            args.estimate is not None,
            args.sessions is not None,
            args.model_used is not None,
            args.model_resets_at,
            args.model_window_hours is not None,
        ]
    )
    flags = [
        (args.used is not None or args.resets_at is not None or args.window_hours is not None)
        and not model_mode,
        args.tokens is not None,
        args.codex,
        args.codex_context,
        model_mode,
    ]
    if sum(1 for f in flags if f) > 1:
        sys.stderr.write(
            "error: use only one of --used/--resets-at/--window-hours, --tokens, --codex, --codex-context, or --model/--usage-json/--estimate\n"
        )
        return 2

    if args.worker and args.tokens is None:
        sys.stderr.write("error: --worker only goes with --tokens\n")
        return 2
    try:
        if args.tokens is not None:
            print(run_tokens(args.tokens, worker=args.worker, write_log=True))
            return 0
        if args.codex:
            print(run_codex())
            return 0
        if args.codex_context:
            print(run_codex_context())
            return 0
        if model_mode:
            windows = load_usage_json(args.usage_json) if args.usage_json else None
            print(
                run_model_pace(
                    model_name=args.model,
                    usage_windows=windows,
                    all_used=args.used,
                    all_resets=args.resets_at,
                    all_hours=args.window_hours,
                    model_used=args.model_used,
                    model_resets=args.model_resets_at,
                    model_hours=args.model_window_hours,
                    estimate_pct=args.estimate,
                    sessions=args.sessions,
                )
            )
            return 0
        missing = []
        if args.used is None:
            missing.append("--used")
        if not args.resets_at:
            missing.append("--resets-at")
        if args.window_hours is None:
            missing.append("--window-hours")
        if missing:
            sys.stderr.write("error: allowance mode needs %s\n" % ", ".join(missing))
            return 2
        print(run_allowance(args.used, args.resets_at, args.window_hours))
        return 0
    except ValueError as e:
        sys.stderr.write("error: %s\n" % e)
        return 2


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class PaceTests(unittest.TestCase):
    def test_token_boundaries(self):
        self.assertEqual(token_zone(0), TOKEN_OK)
        self.assertEqual(token_zone(159_999), TOKEN_OK)
        self.assertEqual(token_zone(160_000), TOKEN_FINISH)
        self.assertEqual(token_zone(199_999), TOKEN_FINISH)
        self.assertEqual(token_zone(200_000), TOKEN_NOW)
        self.assertEqual(token_zone(250_000), TOKEN_NOW)
        self.assertEqual(run_tokens(150000), "TOKENS  150000  OK")

    def test_stricter_zone(self):
        self.assertEqual(stricter("GREEN", "AMBER"), "AMBER")
        self.assertEqual(stricter("GREEN", "RED"), "RED")
        self.assertEqual(stricter("AMBER", "RED", "GREEN"), "RED")

    def test_green_projection(self):
        zone, _ = allowance_zone(used=20, projected=40)
        self.assertEqual(zone, "GREEN")

    def test_amber_from_projection(self):
        zone, reasons = allowance_zone(used=20, projected=90)
        self.assertEqual(zone, "AMBER")
        self.assertTrue(any("85-99" in r for r in reasons))

    def test_amber_from_remaining_beats_green_projection(self):
        # 80% used => 20% left (AMBER), projection 82% is GREEN; stricter is AMBER
        zone, reasons = allowance_zone(used=80, projected=82)
        self.assertEqual(zone, "AMBER")
        self.assertTrue(any("stricter" in r for r in reasons))

    def test_red_from_remaining(self):
        zone, _ = allowance_zone(used=91, projected=92)
        self.assertEqual(zone, "RED")

    def test_red_from_projection(self):
        zone, reasons = allowance_zone(used=40, projected=120)
        self.assertEqual(zone, "RED")
        self.assertTrue(any("100%" in r for r in reasons))

    def test_five_percent_floor(self):
        zone, reasons = allowance_zone(used=95, projected=95)
        self.assertEqual(zone, "RED")
        self.assertTrue(any("5%" in r for r in reasons))
        zone2, _ = allowance_zone(used=96, projected=96)
        self.assertEqual(zone2, "RED")

    def test_linear_from_window_start(self):
        resets = parse_time("2026-11-28T01:00:00+00:00")
        # 84 hours into a 168 hour window, 20% used => projected 40%
        now = resets - timedelta(hours=84)
        projected, how = project_used(20, resets, 168, now, previous=None)
        self.assertAlmostEqual(projected, 40.0, places=4)
        self.assertIn("linear", how)

    def test_rate_from_log_history(self):
        resets = parse_time("2026-11-28T01:00:00+00:00")
        now = resets - timedelta(hours=10)
        previous = {
            "ts": (now - timedelta(hours=2)).isoformat(),
            "used": 20,
        }
        # rose 10% in 2 hours => 5%/hour * 10 hours remaining = +50 => 80
        projected, how = project_used(30, resets, 168, now, previous=previous)
        self.assertAlmostEqual(projected, 80.0, places=4)
        self.assertIn("last log", how)

    def test_stale_allowance(self):
        now = parse_time("2026-11-25T18:00:00+00:00")
        line = run_allowance(
            40,
            "2026-11-25T17:00:00+00:00",
            5,
            now=now,
            write_log=False,
        )
        self.assertTrue(line.startswith("STALE"))

    def test_run_allowance_green_and_log(self,):
        tmp = Path(tempfile.mkdtemp()) / "usage-log.jsonl"
        os.environ["PACE_LOG_PATH"] = str(tmp)
        self.addCleanup(lambda: os.environ.pop("PACE_LOG_PATH", None))
        resets = parse_time("2026-11-28T01:00:00+00:00")
        now = resets - timedelta(hours=84)
        line = run_allowance(20, "2026-11-28T01:00:00Z", 168, now=now, write_log=True)
        self.assertTrue(line.startswith("GREEN"))
        self.assertIn("projected=40%", line)
        recs = [json.loads(l) for l in tmp.read_text().splitlines()]
        self.assertEqual(recs[-1]["zone"], "GREEN")
        self.assertEqual(recs[-1]["used"], 20)

    def test_codex_unavailable(self):
        empty = Path(tempfile.mkdtemp())
        os.environ["PACE_CODEX_SESSIONS"] = str(empty)
        self.addCleanup(lambda: os.environ.pop("PACE_CODEX_SESSIONS", None))
        line = run_codex()
        self.assertTrue(line.startswith("UNAVAILABLE"))
        line2 = run_codex_context()
        self.assertTrue(line2.startswith("UNAVAILABLE"))

    def test_codex_reads_fixture(self):
        root = Path(tempfile.mkdtemp()) / "2026" / "11" / "25"
        root.mkdir(parents=True)
        os.environ["PACE_CODEX_SESSIONS"] = str(root.parent.parent.parent)
        os.environ["PACE_LOG_PATH"] = str(Path(tempfile.mkdtemp()) / "log.jsonl")
        self.addCleanup(lambda: os.environ.pop("PACE_CODEX_SESSIONS", None))
        self.addCleanup(lambda: os.environ.pop("PACE_LOG_PATH", None))
        now = parse_time("2026-11-25T18:00:00+00:00")
        resets = int((now + timedelta(days=3)).timestamp())
        rec = {
            "timestamp": "2026-11-25T17:30:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 58000,
                        "total_tokens": 58100,
                    },
                    "model_context_window": 258400,
                },
                "rate_limits": {
                    "plan_type": "prolite",
                    "primary": {
                        "used_percent": 12.0,
                        "window_minutes": 10080,
                        "resets_at": resets,
                    },
                    "secondary": None,
                },
            },
        }
        (root / "rollout-test.jsonl").write_text(json.dumps(rec) + "\n")
        # monkey now by passing through run_codex's default is wall clock;
        # fixture reset is 3 days from the frozen 'now' above, but run_codex
        # uses real now. Use a reset far in the future instead.
        rec["payload"]["rate_limits"]["primary"]["resets_at"] = int(
            (now_utc() + timedelta(days=4)).timestamp()
        )
        (root / "rollout-test.jsonl").write_text(json.dumps(rec) + "\n")
        line = run_codex()
        self.assertIn("primary", line)
        self.assertIn("used=12%", line)
        self.assertTrue(line.startswith("GREEN") or line.startswith("AMBER") or line.startswith("RED"))
        ctx = run_codex_context()
        self.assertIn("TOKENS  58000  OK", ctx)
        self.assertIn("model-window=258400", ctx)

    def test_codex_prefers_later_event_timestamp(self):
        root = Path(tempfile.mkdtemp())
        os.environ["PACE_CODEX_SESSIONS"] = str(root)
        os.environ["PACE_LOG_PATH"] = str(Path(tempfile.mkdtemp()) / "log.jsonl")
        self.addCleanup(lambda: os.environ.pop("PACE_CODEX_SESSIONS", None))
        self.addCleanup(lambda: os.environ.pop("PACE_LOG_PATH", None))
        future = int((now_utc() + timedelta(days=4)).timestamp())
        older = {
            "timestamp": "2026-11-24T20:39:34Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 200927},
                    "model_context_window": 258400,
                },
                "rate_limits": {
                    "plan_type": "prolite",
                    "primary": {
                        "used_percent": 0.0,
                        "window_minutes": 10080,
                        "resets_at": future,
                    },
                },
            },
        }
        newer = {
            "timestamp": "2026-11-24T20:41:07Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 58235},
                    "model_context_window": 258400,
                },
                "rate_limits": {
                    "plan_type": "prolite",
                    "primary": {
                        "used_percent": 1.0,
                        "window_minutes": 10080,
                        "resets_at": future,
                    },
                },
            },
        }
        old_path = root / "older-but-touched.jsonl"
        new_path = root / "newer-event.jsonl"
        old_path.write_text(json.dumps(older) + "\n")
        new_path.write_text(json.dumps(newer) + "\n")
        # Give the stale file a later mtime, as happens with a long-lived session.
        later = old_path.stat().st_mtime + 10
        os.utime(old_path, (later, later))
        line = run_codex()
        self.assertIn("used=1%", line)
        self.assertNotIn("used=0%", line)
        ctx = run_codex_context()
        self.assertIn("TOKENS  58235  OK", ctx)
        self.assertNotIn("200927", ctx)

    def test_codex_stale(self):
        root = Path(tempfile.mkdtemp()) / "s"
        root.mkdir(parents=True)
        os.environ["PACE_CODEX_SESSIONS"] = str(root)
        os.environ["PACE_LOG_PATH"] = str(Path(tempfile.mkdtemp()) / "log.jsonl")
        self.addCleanup(lambda: os.environ.pop("PACE_CODEX_SESSIONS", None))
        self.addCleanup(lambda: os.environ.pop("PACE_LOG_PATH", None))
        past = int((now_utc() - timedelta(hours=2)).timestamp())
        rec = {
            "timestamp": "2026-11-25T12:00:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 10}},
                "rate_limits": {
                    "primary": {
                        "used_percent": 40.0,
                        "window_minutes": 300,
                        "resets_at": past,
                    }
                },
            },
        }
        (root / "old.jsonl").write_text(json.dumps(rec) + "\n")
        line = run_codex()
        self.assertTrue(line.startswith("STALE"))
        self.assertIn("STALE", line)

    def test_parse_times(self):
        a = parse_time("2026-11-28T01:00:00.167Z")
        b = parse_time(1796151177)
        self.assertEqual(a.tzinfo is not None, True)
        self.assertEqual(b.year, 2026)
        c = parse_time("2026-11-28 01:00")
        self.assertEqual(c.tzinfo is not None, True)


class ModelPaceTests(unittest.TestCase):
    ULTRA = {
        "plan": {
            "status": "ok",
            "plan": "Ultra",
            "windows": [
                {
                    "label": "Weekly · all models",
                    "percentUsed": 28,
                    "resetsAt": "2026-11-28T01:00:00.167Z",
                    "resetsIn": "2d 7h",
                },
                {
                    "label": "Weekly · Fable",
                    "percentUsed": 0,
                    "resetsAt": "2026-11-28T01:00:00.000Z",
                    "resetsIn": "2d 7h",
                },
            ],
        },
        "context": {"tokensUsed": 103390, "status": "ok", "session": "self"},
    }
    MAX_5H = {
        "plan": {
            "status": "ok",
            "plan": "Max",
            "windows": [
                {
                    "label": "5-hour limit",
                    "percentUsed": 18,
                    "resetsAt": "2026-11-25T20:30:00Z",
                    "resetsIn": "2h",
                },
                {
                    "label": "Weekly · all models",
                    "percentUsed": 30,
                    "resetsAt": "2026-11-28T01:00:00Z",
                    "resetsIn": "2d 7h",
                },
                {
                    "label": "Weekly · Fable",
                    "percentUsed": 0,
                    "resetsAt": "2026-11-28T01:00:00Z",
                    "resetsIn": "2d 7h",
                },
            ],
        }
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PACE_LOG_PATH"] = str(self.tmp / "usage-log.jsonl")
        # Pin costs to baked defaults so a later edit of the JSON cannot flake tests.
        os.environ["PACE_COSTS_PATH"] = str(self.tmp / "missing-costs.json")
        self.now = parse_time("2026-11-25T18:00:00Z")

    def tearDown(self):
        os.environ.pop("PACE_LOG_PATH", None)
        os.environ.pop("PACE_COSTS_PATH", None)

    def test_resolve_real_names(self):
        for raw, expected in [
            ("Opus 5.5", "opus"),
            ("claude-opus-5-5", "opus"),
            ("opus", "opus"),
            ("Fable", "fable"),
            ("claude-fable-5-2", "fable"),
            ("sonnet", "sonnet"),
            ("Haiku 5.5", "haiku"),
        ]:
            got = resolve_model(raw)
            self.assertEqual(got["id"], expected, raw)
            self.assertTrue(got["known"], raw)

    def test_sol_is_unknown(self):
        got = resolve_model("Sol")
        self.assertFalse(got["known"])
        self.assertEqual(got["id"], "sol")

    def test_extract_windows_from_get_usage_shape(self):
        windows = extract_usage_windows(self.ULTRA)
        self.assertEqual([w["label"] for w in windows], ["Weekly · all models", "Weekly · Fable"])

    def test_fable_tighter_is_all_models(self):
        out = run_model_pace(
            model_name="fable",
            usage_windows=extract_usage_windows(self.ULTRA),
            now=self.now,
            write_log=False,
        )
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("MODEL fable  tighter=all-models:GREEN"), first)
        self.assertIn("used=28%", first)
        self.assertIn("  all-models  GREEN  used=28%", out)
        self.assertIn("Weekly · Fable  GREEN  used=0%", out)
        self.assertNotIn("per-model  none", out)

    def test_opus_has_no_per_model_window(self):
        out = run_model_pace(
            model_name="Opus 5.5",
            usage_windows=extract_usage_windows(self.ULTRA),
            now=self.now,
            write_log=False,
        )
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("MODEL opus (Opus 5.5)  tighter=all-models:GREEN"), first)
        self.assertIn('per-model  none  no "Weekly · Opus" window in this meter', out)

    def test_tighter_prefers_hotter_fable_window(self):
        data = json.loads(json.dumps(self.ULTRA))
        data["plan"]["windows"][1]["percentUsed"] = 92
        out = run_model_pace(
            model_name="fable",
            usage_windows=extract_usage_windows(data),
            now=self.now,
            write_log=False,
        )
        first = out.splitlines()[0]
        self.assertIn("tighter=per-model:RED", first)
        self.assertIn("used=92%", first)

    def test_other_family_window_does_not_tighten_unrelated_model(self):
        data = json.loads(json.dumps(self.ULTRA))
        data["plan"]["windows"][1]["percentUsed"] = 92
        out = run_model_pace(
            model_name="sonnet",
            usage_windows=extract_usage_windows(data),
            now=self.now,
            write_log=False,
        )
        first = out.splitlines()[0]
        self.assertIn("tighter=all-models:GREEN", first)
        self.assertIn("used=28%", first)
        self.assertNotIn("tighter=per-model:RED", first)
        # The Fable row is still shown, but it is not the tighter cap for sonnet.
        self.assertIn("Weekly · Fable  RED  used=92%", out)

    def test_flags_per_window(self):
        out = run_model_pace(
            model_name="fable",
            all_used=28,
            all_resets="2026-11-28T01:00:00Z",
            all_hours=168,
            model_used=0,
            model_resets="2026-11-28T01:00:00Z",
            model_hours=168,
            now=self.now,
            write_log=False,
        )
        self.assertIn("tighter=all-models:GREEN", out)
        self.assertIn("used=28%", out.splitlines()[0])
        self.assertIn("Weekly · Fable  GREEN  used=0%", out)

    def test_five_hour_window_is_reported(self):
        out = run_model_pace(
            model_name="sonnet",
            usage_windows=extract_usage_windows(self.MAX_5H),
            now=self.now,
            write_log=False,
        )
        self.assertIn("  5-hour  GREEN  used=18%", out)
        self.assertIn("  all-models  GREEN  used=30%", out)
        # 30% weekly is the tighter used among GREEN windows
        self.assertIn("tighter=all-models:GREEN  used=30%", out.splitlines()[0])

    def test_sessions_splits_allowance_and_totals_estimate(self):
        out = run_model_pace(
            model_name="sonnet",
            usage_windows=extract_usage_windows(self.ULTRA),
            estimate_pct=10,
            sessions=4,
            now=self.now,
            write_log=False,
        )
        # 28% used -> 72% left, minus 5% reserve = 67% spendable, / 4 = 16.75% each
        self.assertIn("PARALLEL  4 workers x 10% = 40% total on sonnet", out)
        self.assertIn("ESTIMATE  job=40% on sonnet", out)
        self.assertIn("SHARE  4 workers  67% spendable (72% left minus the 5% reserve)  =  16.8% each", out)

    def test_sessions_one_has_no_parallel_line_and_zero_left_is_safe(self):
        out = run_model_pace(
            model_name="sonnet",
            usage_windows=extract_usage_windows(self.ULTRA),
            sessions=1,
            now=self.now,
            write_log=False,
        )
        self.assertNotIn("PARALLEL", out)
        self.assertIn("SHARE  1 workers  67% spendable", out)
        data = json.loads(json.dumps(self.ULTRA))
        data["plan"]["windows"][0]["percentUsed"] = 97
        out2 = run_model_pace(
            model_name="sonnet",
            usage_windows=extract_usage_windows(data),
            sessions=3,
            now=self.now,
            write_log=False,
        )
        self.assertIn("SHARE  3 workers  0% spendable", out2)
        with self.assertRaises(ValueError):
            run_model_pace(model_name="sonnet", usage_windows=extract_usage_windows(self.ULTRA), sessions=0, now=self.now, write_log=False)

    def test_worker_tokens_writes_one_log_line(self):
        log = Path(os.environ["PACE_LOG_PATH"])
        self.assertEqual(run_tokens(161000, worker="job4", write_log=True), "TOKENS  161000  FINISH STEP, THEN HAND OVER")
        rows = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["worker"], rows[0]["tokens"]), ("worker", "job4", 161000))
        # No worker name: nothing logged
        run_tokens(10, write_log=True)
        self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_estimate_scales_by_relative_cost(self):
        # 10% on sonnet -> 40% on fable (cost 4), added to 28% all-models = 68%
        out = run_model_pace(
            model_name="fable",
            usage_windows=extract_usage_windows(self.ULTRA),
            estimate_pct=10,
            now=self.now,
            write_log=False,
        )
        self.assertIn("ESTIMATE  job=10% on sonnet  →  40% on fable  new-used=68% on Weekly · all models  FITS", out)
        self.assertIn("SUGGEST  for routine parts:", out)
        self.assertIn("haiku (job=0.5% FITS", out)
        self.assertIn("sonnet (job=10% FITS", out)
        self.assertIn("opus (job=20% FITS", out)

        out_opus = run_model_pace(
            model_name="opus",
            usage_windows=extract_usage_windows(self.ULTRA),
            estimate_pct=10,
            now=self.now,
            write_log=False,
        )
        self.assertIn("→  20% on opus  new-used=48% on Weekly · all models  FITS", out_opus)

        out_haiku = run_model_pace(
            model_name="haiku",
            usage_windows=extract_usage_windows(self.ULTRA),
            estimate_pct=10,
            now=self.now,
            write_log=False,
        )
        self.assertIn("→  0.5% on haiku  new-used=28.5% on Weekly · all models  FITS", out_haiku)
        # Already cheaper than default: no suggest required
        self.assertNotIn("SUGGEST", out_haiku)

    def test_estimate_does_not_fit_and_suggests(self):
        data = json.loads(json.dumps(self.ULTRA))
        data["plan"]["windows"][0]["percentUsed"] = 60
        # Near reset so the current 60% does not already project past 100%.
        now = parse_time("2026-11-28T00:30:00Z")
        # 10% on sonnet -> 40% on fable, 60+40=100, DOES NOT FIT
        out = run_model_pace(
            model_name="fable",
            usage_windows=extract_usage_windows(data),
            estimate_pct=10,
            now=now,
            write_log=False,
        )
        self.assertIn("DOES NOT FIT", out)
        self.assertIn("SUGGEST  for routine parts:", out)
        self.assertIn("sonnet (job=10% FITS", out)
        self.assertIn("haiku (job=0.5% FITS", out)

    def test_estimate_tight_near_reserve(self):
        data = json.loads(json.dumps(self.ULTRA))
        data["plan"]["windows"][0]["percentUsed"] = 70
        now = parse_time("2026-11-28T00:30:00Z")
        # 10% on sonnet -> 20% on opus, 70+20=90% used, remaining 10% is AMBER, not >=95
        out = run_model_pace(
            model_name="opus",
            usage_windows=extract_usage_windows(data),
            estimate_pct=10,
            now=now,
            write_log=False,
        )
        self.assertIn("new-used=90%", out)
        self.assertIn("TIGHT", out)

    def test_costs_file_override(self):
        costs = {
            "default_model": "sonnet",
            "models": {"opus": {"cost": 3.0}},
        }
        path = self.tmp / "model-costs.json"
        path.write_text(json.dumps(costs))
        os.environ["PACE_COSTS_PATH"] = str(path)
        out = run_model_pace(
            model_name="opus",
            usage_windows=extract_usage_windows(self.ULTRA),
            estimate_pct=10,
            now=self.now,
            write_log=False,
        )
        self.assertIn("→  30% on opus  new-used=58%", out)

    def test_usage_json_cli_and_stdin_roundtrip(self):
        usage = self.tmp / "usage.json"
        usage.write_text(json.dumps(self.ULTRA))
        log = self.tmp / "cli-log.jsonl"
        env = os.environ.copy()
        env["PACE_LOG_PATH"] = str(log)
        env["PACE_COSTS_PATH"] = str(self.tmp / "missing-costs.json")
        import subprocess
        script = Path(__file__).resolve()
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--model",
                "fable",
                "--usage-json",
                str(usage),
                "--estimate",
                "10",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("MODEL fable  tighter=all-models:", proc.stdout)
        self.assertIn("ESTIMATE  job=10% on sonnet  →  40% on fable", proc.stdout)
        # stdin form
        proc2 = subprocess.run(
            [sys.executable, str(script), "--model", "opus", "--usage-json", "-"],
            input=json.dumps(self.ULTRA),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertIn('no "Weekly · Opus" window', proc2.stdout)

    def test_existing_used_flag_still_single_line(self):
        line = run_allowance(
            20,
            "2026-11-28T01:00:00Z",
            168,
            now=parse_time("2026-11-24T13:00:00Z"),
            write_log=False,
        )
        self.assertTrue(line.startswith("GREEN  used=20%"), line)
        self.assertNotIn("\n", line)


def run_self_tests() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(PaceTests))
    suite.addTests(loader.loadTestsFromTestCase(ModelPaceTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
