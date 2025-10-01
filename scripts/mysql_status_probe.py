#!/usr/bin/env python3
"""MySQL sandbox telemetry helper.

This script samples key server statistics so non-DBA teammates can understand
how many connections and queries are active while running PDR workloads.
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import mysql.connector


STATUS_KEYS = [
    "Threads_connected",
    "Threads_running",
    "Connections",
    "Questions",
    "Aborted_clients",
    "Aborted_connects",
]


def _fetch_status(cursor) -> Dict[str, Any]:
    cursor.execute("SHOW GLOBAL STATUS")
    rows = cursor.fetchall()
    status = {key: value for key, value in rows if key in STATUS_KEYS}
    return status


def _fetch_processlist(cursor) -> List[Dict[str, Any]]:
    cursor.execute("SHOW PROCESSLIST")
    fields = [desc[0] for desc in cursor.description]
    return [dict(zip(fields, row)) for row in cursor.fetchall()]


def _connect(args) -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        connection_timeout=args.connect_timeout,
    )


def _write_snapshot(output_dir: Path, label: str, payload: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"{label}_{ts}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    csv_path = output_dir / f"{label}_{ts}.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        for key, value in payload["status"].items():
            writer.writerow([key, value])


def sample(args) -> None:
    connection = _connect(args)
    cursor = connection.cursor()

    snapshots = []
    start = time.time()
    try:
        while True:
            now = datetime.utcnow()
            status = _fetch_status(cursor)
            processes = _fetch_processlist(cursor)

            snapshot = {
                "captured_at_utc": now.isoformat(timespec="seconds") + "Z",
                "status": status,
                "processlist": processes,
            }
            snapshots.append(snapshot)

            overview = (
                f"[{snapshot['captured_at_utc']}] "
                f"Threads_connected={status.get('Threads_connected')} "
                f"Threads_running={status.get('Threads_running')} "
                f"Total_connections={status.get('Connections')} "
                f"Total_queries={status.get('Questions')}"
            )
            print(overview, flush=True)

            _write_snapshot(Path(args.output_dir), "mysql_status", snapshot)

            if args.duration and (time.time() - start) >= args.duration:
                break
            time.sleep(args.interval)
    finally:
        cursor.close()
        connection.close()

    summary = {
        "samples": len(snapshots),
        "max_threads_connected": max(int(s["status"]["Threads_connected"]) for s in snapshots),
        "max_threads_running": max(int(s["status"]["Threads_running"]) for s in snapshots),
        "total_runtime_sec": round(time.time() - start, 2),
    }
    summary_path = Path(args.output_dir) / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print("Summary:", json.dumps(summary, indent=2))


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="MySQL host")
    parser.add_argument("--port", type=int, default=3306, help="MySQL port")
    parser.add_argument("--user", required=True, help="MySQL user")
    parser.add_argument("--password", required=True, help="MySQL password")
    parser.add_argument("--database", default=None, help="Database to connect to")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--duration", type=int, default=0, help="How long to poll (0 = until interrupted)")
    parser.add_argument("--output-dir", default="mysql-diagnostics", help="Directory for snapshots")
    parser.add_argument("--connect-timeout", type=int, default=10, help="Connection timeout seconds")
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    args = parse_args(argv)
    sample(args)


if __name__ == "__main__":
    main(sys.argv[1:])