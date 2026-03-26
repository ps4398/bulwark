"""
Persistent statistics for node health checks.

Two tables:
  node_checks     — raw per-check records (every monitor_interval seconds)
  downtime_events — aggregated incidents (open when unhealthy, closed on recovery)

probe_src distinguishes the vantage point:
  'local'           — checks from the management bridge (where the bot runs)
  '<bridge_name>'   — TCP probes SSHed from single-port bridge nodes
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_checks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    node_name  TEXT    NOT NULL,
    probe_src  TEXT    NOT NULL DEFAULT 'local',
    healthy    INTEGER NOT NULL,
    icmp_ok    INTEGER,
    latency_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_nc_node_ts  ON node_checks(node_name, ts);
CREATE INDEX IF NOT EXISTS idx_nc_src_node ON node_checks(probe_src, node_name, ts);

CREATE TABLE IF NOT EXISTS downtime_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_name  TEXT    NOT NULL,
    probe_src  TEXT    NOT NULL DEFAULT 'local',
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    duration_s INTEGER
);
CREATE INDEX IF NOT EXISTS idx_de_node ON downtime_events(node_name, started_at);
"""


class StatsDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Write

    def record_check(
        self,
        node_name: str,
        healthy: bool,
        icmp_ok: Optional[bool] = None,
        latency_ms: Optional[float] = None,
        probe_src: str = "local",
    ) -> None:
        ts = int(time.time())
        self._conn.execute(
            "INSERT INTO node_checks (ts, node_name, probe_src, healthy, icmp_ok, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                ts,
                node_name,
                probe_src,
                int(healthy),
                int(icmp_ok) if icmp_ok is not None else None,
                round(latency_ms, 2) if latency_ms is not None else None,
            ),
        )
        if not healthy:
            self._open_downtime(node_name, probe_src, ts)
        else:
            self._close_downtime(node_name, probe_src, ts)

    def _open_downtime(self, node_name: str, probe_src: str, ts: int) -> None:
        existing = self._conn.execute(
            "SELECT id FROM downtime_events "
            "WHERE node_name=? AND probe_src=? AND ended_at IS NULL",
            (node_name, probe_src),
        ).fetchone()
        if not existing:
            self._conn.execute(
                "INSERT INTO downtime_events (node_name, probe_src, started_at) "
                "VALUES (?, ?, ?)",
                (node_name, probe_src, ts),
            )

    def _close_downtime(self, node_name: str, probe_src: str, ts: int) -> None:
        row = self._conn.execute(
            "SELECT id, started_at FROM downtime_events "
            "WHERE node_name=? AND probe_src=? AND ended_at IS NULL",
            (node_name, probe_src),
        ).fetchone()
        if row:
            duration = ts - row[1]
            self._conn.execute(
                "UPDATE downtime_events SET ended_at=?, duration_s=? WHERE id=?",
                (ts, duration, row[0]),
            )

    # ------------------------------------------------------------------
    # Read

    def uptime_pct(
        self,
        node_name: str,
        hours: int = 24,
        probe_src: str = "local",
    ) -> Optional[float]:
        """Uptime % over last N hours. None if no data."""
        since = int(time.time()) - hours * 3600
        rows = self._conn.execute(
            "SELECT healthy, COUNT(*) FROM node_checks "
            "WHERE node_name=? AND probe_src=? AND ts>=? GROUP BY healthy",
            (node_name, probe_src, since),
        ).fetchall()
        total = sum(r[1] for r in rows)
        if not total:
            return None
        ok = sum(r[1] for r in rows if r[0] == 1)
        return ok / total * 100

    def avg_latency(
        self,
        node_name: str,
        hours: int = 24,
        probe_src: str = "local",
    ) -> Optional[float]:
        since = int(time.time()) - hours * 3600
        row = self._conn.execute(
            "SELECT AVG(latency_ms) FROM node_checks "
            "WHERE node_name=? AND probe_src=? AND ts>=? AND latency_ms IS NOT NULL",
            (node_name, probe_src, since),
        ).fetchone()
        return round(row[0], 1) if row and row[0] is not None else None

    def recent_incidents(self, node_name: str, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT probe_src, started_at, ended_at, duration_s "
            "FROM downtime_events WHERE node_name=? "
            "ORDER BY started_at DESC LIMIT ?",
            (node_name, limit),
        ).fetchall()
        return [
            {
                "probe_src":  r[0],
                "started_at": r[1],
                "ended_at":   r[2],
                "duration_s": r[3],
            }
            for r in rows
        ]

    def all_uptime_summary(
        self, hours: int = 24, probe_src: str = "local"
    ) -> dict[str, Optional[float]]:
        """Returns {node_name: uptime_pct} for all nodes over last N hours."""
        since = int(time.time()) - hours * 3600
        rows = self._conn.execute(
            "SELECT node_name, healthy, COUNT(*) FROM node_checks "
            "WHERE probe_src=? AND ts>=? GROUP BY node_name, healthy",
            (probe_src, since),
        ).fetchall()
        totals: dict[str, int] = {}
        ok_map: dict[str, int] = {}
        for name, healthy, cnt in rows:
            totals[name] = totals.get(name, 0) + cnt
            if healthy:
                ok_map[name] = ok_map.get(name, 0) + cnt
        return {
            name: (ok_map.get(name, 0) / total * 100)
            for name, total in totals.items()
        }

    def close(self) -> None:
        self._conn.close()
