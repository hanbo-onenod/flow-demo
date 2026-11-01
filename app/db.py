"""Demo database: fictional customers and orders in SQLite.

Everything here is made up. Customer names are Chinese on purpose so the
CSV encoding problem shows up when the file is opened in Excel.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

SURNAMES = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂"
CITIES = ["上海", "深圳", "Singapore", "Dubai", "London", "杭州", "Jakarta", "Tokyo"]


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_code TEXT NOT NULL,
            name TEXT NOT NULL,
            city TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_no INTEGER NOT NULL,
            customer_code TEXT NOT NULL,
            order_date TEXT NOT NULL,
            amount_cents INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_customers_customer_code
            ON customers (customer_code);
        """
    )


def customers_by_code(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """First name and city for each customer_code, in table order.

    Same row a per-order ``SELECT name, city FROM customers WHERE
    customer_code = ?`` ``fetchone()`` used to return: the lowest rowid.
    """
    found: dict[str, tuple[str, str]] = {}
    for row in conn.execute("SELECT customer_code, name, city FROM customers ORDER BY rowid"):
        code = row["customer_code"]
        if code not in found:
            found[code] = (row["name"], row["city"])
    return found


def make_order_numbers(n: int, rng: random.Random) -> list[int]:
    # Draws with replacement, so duplicates happen now and then: for n=300,
    # about one run in four. That is the planted flaky test.
    return [rng.randint(1, n * 700) for _ in range(n)]


def seed(conn: sqlite3.Connection, customers: int = 20000, orders: int = 10000, seed_value: int | None = 42) -> None:
    rng = random.Random(seed_value)
    create_schema(conn)
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [
            (f"C{i:05d}", rng.choice(SURNAMES) + rng.choice(GIVEN) + rng.choice(GIVEN), rng.choice(CITIES))
            for i in range(customers)
        ],
    )
    start = date(2026, 1, 1)
    numbers = make_order_numbers(orders, rng)
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?)",
        [
            (
                numbers[i],
                f"C{rng.randrange(customers):05d}",
                (start + timedelta(days=rng.randrange(300))).isoformat(),
                rng.randrange(100, 500_000),
            )
            for i in range(orders)
        ],
    )
    conn.commit()
