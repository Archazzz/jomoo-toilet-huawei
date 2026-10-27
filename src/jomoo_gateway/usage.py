"""Private, sampled usage history. Requests and guessed actuator outcomes are never events."""

import json
import math
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from datetime import time as daytime
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")
FLAGS = ("large_flush_running", "small_flush_running", "foam_running")
MODES = frozenset({"hip", "bidet", "assist", "dry", "nozzle_clean"})
METRICS = ("flush_large", "flush_small", "foam", "seated", "seated_hip")


class UsageStore:
    """Atomic sampled history, independent of the command ledger."""

    def __init__(self, path: Path, *, max_gap: float = 120, retention_days: int = 90):
        if not 5 <= max_gap <= 300 or not 31 <= retention_days <= 365:
            raise ValueError("报告间隔须为 5–300 秒，保留期须为 31–365 天")
        self.max_gap = max_gap
        self.retention_days = retention_days
        self.seen: set[str] = set()
        self.db = sqlite3.connect(path, timeout=0.05)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_meta(version INTEGER NOT NULL);
            INSERT INTO usage_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM usage_meta);
            CREATE TABLE IF NOT EXISTS samples(
                device TEXT, at REAL, state TEXT, PRIMARY KEY(device, at));
            CREATE TABLE IF NOT EXISTS events(
                device TEXT, at REAL, kind TEXT, lower_bound REAL,
                PRIMARY KEY(device, at, kind));
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY, device TEXT, start REAL, last_seen REAL,
                end REAL, known_start INTEGER, complete INTEGER DEFAULT 0,
                hip INTEGER DEFAULT 0, modes TEXT DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS session_device_start ON sessions(device, start);
            CREATE TABLE IF NOT EXISTS gaps(
                device TEXT, start REAL, end REAL, reason TEXT,
                PRIMARY KEY(device,start,end));
            CREATE TABLE IF NOT EXISTS cursor(
                device TEXT PRIMARY KEY, at REAL, state TEXT, session_id INTEGER);
        """)
        if self.db.execute("SELECT version FROM usage_meta").fetchone()[0] != 1:
            self.db.close()
            raise ValueError("不支持的报告数据库版本")
        self.prune(time.time())

    def close(self):
        self.db.close()

    def prune(self, now: float):
        cutoff = now - self.retention_days * 86400
        with self.db:
            for table in ("samples", "events"):
                self.db.execute(f"DELETE FROM {table} WHERE at < ?", (cutoff,))
            self.db.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
            self.db.execute("DELETE FROM cursor WHERE at < ?", (cutoff,))
            self.db.execute("DELETE FROM gaps WHERE end < ?", (cutoff,))
        self.next_prune = now + 86400

    def observe(self, device: str, state: dict | None, at: float | None = None):
        at = time.time() if at is None else at
        if not math.isfinite(at):
            raise ValueError("采样时间无效")
        # Only persist measurements used by the report, not identifiers/settings/raw BLE frames.
        state = (
            None
            if state is None
            else {
                k: v
                for k, v in state.items()
                if (k in (*FLAGS, "seated") and isinstance(v, bool))
                or (k == "wash_mode" and v in MODES | {"idle"})
            }
        )
        state = state or None
        if at >= self.next_prune:
            self.prune(at)
        with self.db:
            old = self.db.execute("SELECT * FROM cursor WHERE device=?", (device,)).fetchone()
            if old and at <= old["at"]:
                return  # Duplicate or out-of-order delivery cannot create another edge.
            previous = json.loads(old["state"]) if old else None
            continuous = bool(
                old
                and device in self.seen
                and previous is not None
                and state is not None
                and at - old["at"] <= self.max_gap
            )
            if old and not continuous:
                reason = "restart" if device not in self.seen else "missing_observation"
                self.db.execute(
                    "INSERT OR IGNORE INTO gaps VALUES(?,?,?,?)", (device, old["at"], at, reason)
                )
            sid = old["session_id"] if old else None
            if not continuous and sid:
                self.db.execute("UPDATE sessions SET end=last_seen WHERE id=?", (sid,))
                sid = None
            if state is not None:
                if continuous:
                    for flag, kind in zip(
                        FLAGS, ("flush_large", "flush_small", "foam"), strict=True
                    ):
                        if previous.get(flag) is False and state.get(flag) is True:
                            self._event(device, at, kind, old["at"])
                    mode = state.get("wash_mode")
                    if (
                        mode in MODES
                        and previous.get("wash_mode") is not None
                        and mode != previous["wash_mode"]
                    ):
                        self._event(device, at, mode, old["at"])
                seated = state.get("seated")
                if seated is True:
                    if sid is None:
                        known = continuous and previous.get("seated") is False
                        sid = self.db.execute(
                            "INSERT INTO sessions(device,start,last_seen,known_start) "
                            "VALUES(?,?,?,?)",
                            (device, at, at, int(known)),
                        ).lastrowid
                        if known:
                            self._event(device, at, "seated", old["at"])
                    row = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
                    modes = set(json.loads(row["modes"]))
                    if state.get("wash_mode") in MODES:
                        modes.add(state["wash_mode"])
                    if "hip" in modes and not row["hip"] and row["known_start"]:
                        # This metric belongs to the session's start day, once per session.
                        self._event(device, row["start"], "seated_hip", row["start"])
                    self.db.execute(
                        "UPDATE sessions SET last_seen=?,hip=?,modes=? WHERE id=?",
                        (at, int("hip" in modes), json.dumps(sorted(modes)), sid),
                    )
                elif sid:
                    # Missing occupancy is a break, never an inferred departure.
                    complete = seated is False and continuous and previous.get("seated") is True
                    self.db.execute(
                        "UPDATE sessions SET end=?,complete=? WHERE id=?",
                        (at if complete else old["at"], int(complete), sid),
                    )
                    sid = None
            encoded = json.dumps(state, separators=(",", ":"))
            self.db.execute("INSERT INTO samples VALUES(?,?,?)", (device, at, encoded))
            self.db.execute(
                "INSERT INTO cursor VALUES(?,?,?,?) ON CONFLICT(device) DO UPDATE SET "
                "at=excluded.at,state=excluded.state,session_id=excluded.session_id",
                (device, at, encoded, sid),
            )
        self.seen.add(device)

    def _event(self, device, at, kind, lower_bound):
        self.db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)", (device, at, kind, lower_bound)
        )

    def report(self, device: str, start: str, days: int = 7, *, now: float | None = None):
        if not 1 <= days <= 31:
            raise ValueError("一次最多查询 31 天")
        first = date.fromisoformat(start)
        if first.isoformat() != start:
            raise ValueError("日期须为 YYYY-MM-DD")
        now = time.time() if now is None else now
        boundaries = [
            datetime.combine(first + timedelta(days=i), daytime(), ZONE).timestamp()
            for i in range(days + 1)
        ]
        lo, hi = boundaries[0], boundaries[-1]
        samples = self.db.execute(
            "SELECT at,state FROM samples WHERE device=? AND at>=? AND at<? ORDER BY at",
            (device, lo, hi),
        ).fetchall()
        events = self.db.execute(
            "SELECT at,kind,lower_bound FROM events WHERE device=? AND at>=? AND at<? ORDER BY at",
            (device, lo, hi),
        ).fetchall()
        gaps = [
            dict(r)
            for r in self.db.execute(
                "SELECT start,end,reason FROM gaps WHERE device=? AND start<? AND end>?",
                (device, hi, lo),
            )
        ]
        sessions = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM sessions WHERE device=? AND start<? AND COALESCE(end,last_seen)>=? "
                "ORDER BY start DESC",
                (device, hi, lo),
            )
        ]
        rows = []
        for i in range(days):
            a, b = boundaries[i : i + 2]
            day_samples = [s for s in samples if a <= s["at"] < b]
            observed = any(s["state"] != "null" for s in day_samples)
            counts = {
                k: sum(e["kind"] == k and a <= e["at"] < b for e in events) if observed else None
                for k in METRICS
            }
            seconds = sum(
                max(0, min(s["end"] or s["last_seen"], b) - max(s["start"], a)) for s in sessions
            )
            rows.append(
                {
                    "date": (first + timedelta(days=i)).isoformat(),
                    **counts,
                    "seated_seconds_estimate": round(seconds) if observed else None,
                    "samples": len(day_samples),
                    "observed": observed,
                    "failed_samples": sum(s["state"] == "null" for s in day_samples),
                    "interrupted": any(g["start"] < b and g["end"] > a for g in gaps),
                }
            )
        cursor = self.db.execute("SELECT at,state FROM cursor WHERE device=?", (device,)).fetchone()
        for session in sessions:
            session["modes"] = json.loads(session["modes"])
            session["complete"] = bool(session["complete"] and session["known_start"])
            session["known_start"] = bool(session["known_start"])
            session["ongoing"] = session["end"] is None
            session["duration_seconds_estimate"] = round(
                (session["end"] or session["last_seen"]) - session["start"]
            )
            del session["device"]
        observed_days = sum(r["observed"] for r in rows)
        return {
            "schema_version": 1,
            "device": device,
            "timezone": str(ZONE),
            "start": start,
            "days": days,
            "generated_at": now,
            "collection": {
                "method": "sampled_edges",
                "retention_days": self.retention_days,
                "max_gap_seconds": self.max_gap,
                "observed_days": observed_days,
                "last_observed_at": cursor["at"] if cursor else None,
                "stale": not cursor
                or cursor["state"] == "null"
                or now - cursor["at"] > self.max_gap,
                "limitations": [
                    "short_events_may_be_missed",
                    "duration_is_estimated",
                    "occupancy_is_not_defecation",
                    "no_person_identification",
                ],
            },
            "totals": {k: sum(r[k] or 0 for r in rows) if observed_days else None for k in METRICS},
            "daily": rows,
            "sessions": sessions,
            "gaps": gaps,
            "events": [dict(e) for e in events],
        }
