#!/usr/bin/env python3
"""Create a sample SQLite DB with prior reports + result rows for OFC demos."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples" / "archive.sqlite3"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE prior_reports (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            year INTEGER,
            body_md TEXT NOT NULL
        );
        CREATE TABLE quarterly_results (
            id INTEGER PRIMARY KEY,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT,
            period TEXT NOT NULL,
            notes TEXT
        );
        """
    )
    cur.executemany(
        "INSERT INTO prior_reports (title, year, body_md) VALUES (?, ?, ?)",
        [
            (
                "Q1 Operations Summary",
                2025,
                """# Q1 Operations Summary

## Executive overview
Throughput remained within planned bands. Two incidents required follow-up.

## Key results
- Tickets closed: 412
- Mean time to resolve: 6.4 hours
- Critical incidents: 2

## Outlook
Focus next quarter on backlog aging over 14 days.
""",
            ),
            (
                "Q2 Operations Summary",
                2025,
                """# Q2 Operations Summary

## Executive overview
Stabilization work reduced critical incidents. Capacity for planned work increased.

## Key results
- Tickets closed: 458
- Mean time to resolve: 5.1 hours
- Critical incidents: 0

## Outlook
Maintain staffing for change windows; continue weekly metrics review.
""",
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO quarterly_results (metric, value, unit, period, notes) VALUES (?, ?, ?, ?, ?)",
        [
            ("tickets_closed", 501, "count", "2025-Q3", "Includes backlog sweep"),
            ("mttr_hours", 4.8, "hours", "2025-Q3", None),
            ("critical_incidents", 1, "count", "2025-Q3", "Network flap 12 Aug"),
            ("sla_met_pct", 97.2, "percent", "2025-Q3", "Target 95%"),
            ("change_success_pct", 94.0, "percent", "2025-Q3", None),
        ],
    )
    conn.commit()
    conn.close()
    print(f"Wrote {OUT}")
    print("In OFC Sources:")
    print(f"  dialect=sqlite  dsn={OUT}")
    print("  results query: SELECT metric, value, unit, period, notes FROM quarterly_results")
    print("  examples query: SELECT title, year, body_md FROM prior_reports")
    print("  example_body_column: body_md")


if __name__ == "__main__":
    main()
