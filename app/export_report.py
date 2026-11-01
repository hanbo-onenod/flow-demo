"""Export orders to CSV.

Usage:
    python -m app.export_report out.csv [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--db path.sqlite]

The file starts with a UTF-8 BOM so Excel on Windows and Mac shows Chinese names.
``--start`` and ``--end`` are optional and inclusive. Omit both to export every order.

Known problems (planted for the demo, do not fix them outside a task):
- No Excel export.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from datetime import date, datetime

from app import db

HEADER = ["order_no", "order_date", "customer", "city", "amount"]

# Exactly four digits, two digits, two digits. strptime would also accept
# "2026-3-1", which is not a valid YYYY-MM-DD date for this export.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _coerce_date(value: date | str | None) -> date | None:
    """Return a calendar date, or None when this bound is omitted.

    Strings must be real ``YYYY-MM-DD`` dates. ``datetime.date`` values are
    accepted as-is. Anything else raises ``ValueError`` naming the bad value.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            parsed = None
        if parsed is not None and parsed.isoformat() == value:
            return parsed
    shown = value if isinstance(value, str) else repr(value)
    raise ValueError(f"invalid date {shown}: expected YYYY-MM-DD")


def _validated_range(
    start: date | str | None,
    end: date | str | None,
) -> tuple[date | None, date | None]:
    start_d = _coerce_date(start)
    end_d = _coerce_date(end)
    if start_d is not None and end_d is not None and start_d > end_d:
        raise ValueError(
            "the start date is after the end date "
            f"({start_d.isoformat()} is after {end_d.isoformat()})"
        )
    return start_d, end_d


def fetch_rows(
    conn: sqlite3.Connection,
    start: date | None = None,
    end: date | None = None,
) -> list[list[str]]:
    customers = db.customers_by_code(conn)
    # No bounds: keep the original query so the unfiltered file stays byte-identical.
    if start is None and end is None:
        orders = conn.execute("SELECT * FROM orders ORDER BY order_date, order_no")
    else:
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("order_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("order_date <= ?")
            params.append(end.isoformat())
        orders = conn.execute(
            "SELECT * FROM orders WHERE "
            + " AND ".join(clauses)
            + " ORDER BY order_date, order_no",
            params,
        )
    rows = []
    for order in orders:
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


def export_csv(
    conn: sqlite3.Connection,
    path: str,
    start: date | str | None = None,
    end: date | str | None = None,
) -> int:
    # Validate before opening the file so a bad date or range cannot create
    # or overwrite the output.
    start_d, end_d = _validated_range(start, end)
    rows = fetch_rows(conn, start=start_d, end=end_d)
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
    parser.add_argument(
        "--start",
        default=None,
        help="First order date to include, YYYY-MM-DD (inclusive)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Last order date to include, YYYY-MM-DD (inclusive)",
    )
    parser.add_argument("--db", default=None, help="SQLite file; default is a freshly seeded in-memory demo DB")
    args = parser.parse_args(argv)
    try:
        start, end = _validated_range(args.start, args.end)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.db:
        conn = db.connect(args.db)
    else:
        conn = db.connect()
        db.seed(conn)
    count = export_csv(conn, args.out, start=start, end=end)
    print(f"exported {count} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
