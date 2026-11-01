"""Export orders to CSV.

Usage:
    python -m app.export_report out.csv [--db path.sqlite]

The file starts with a UTF-8 BOM so Excel on Windows and Mac shows Chinese names.

Known problems (planted for the demo, do not fix them outside a task):
- No date filter and no Excel export.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys

from app import db

HEADER = ["order_no", "order_date", "customer", "city", "amount"]


def fetch_rows(conn: sqlite3.Connection) -> list[list[str]]:
    customers = db.customers_by_code(conn)
    rows = []
    for order in conn.execute("SELECT * FROM orders ORDER BY order_date, order_no"):
        name, city = customers[order["customer_code"]]
        rows.append(
            [
                str(order["order_no"]),
                order["order_date"],
                name,
                city,
                f"{order['amount_cents'] / 100:.2f}",
            ]
        )
    return rows


def export_csv(conn: sqlite3.Connection, path: str) -> int:
    rows = fetch_rows(conn)
    # Leading U+FEFF is the UTF-8 BOM (EF BB BF). Excel uses it to detect UTF-8.
    # Everything after it is the same csv.writer output as before this change.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\ufeff")
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export orders to CSV")
    parser.add_argument("out")
    parser.add_argument("--db", default=None, help="SQLite file; default is a freshly seeded in-memory demo DB")
    args = parser.parse_args(argv)
    if args.db:
        conn = db.connect(args.db)
    else:
        conn = db.connect()
        db.seed(conn)
    count = export_csv(conn, args.out)
    print(f"exported {count} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
