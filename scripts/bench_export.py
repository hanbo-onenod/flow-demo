"""Measure how long the CSV export takes. The acceptance bot uses this.

Usage:
    python scripts/bench_export.py [--orders 10000] [--runs 3]

Prints one JSON line, for example:
    {"orders": 10000, "runs": 3, "best_seconds": 4.12, "median_seconds": 4.20}
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, export_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=10000)
    parser.add_argument("--customers", type=int, default=20000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    conn = db.connect()
    db.seed(conn, customers=args.customers, orders=args.orders)
    times = []
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "orders.csv")
        for _ in range(args.runs):
            start = time.perf_counter()
            export_report.export_csv(conn, out)
            times.append(time.perf_counter() - start)
    print(
        json.dumps(
            {
                "orders": args.orders,
                "runs": args.runs,
                "best_seconds": round(min(times), 3),
                "median_seconds": round(statistics.median(times), 3),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
