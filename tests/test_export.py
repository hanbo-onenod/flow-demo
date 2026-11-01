from __future__ import annotations

import csv
import os
import random

import pytest

from app import db, export_report


@pytest.fixture
def small_db():
    conn = db.connect()
    db.seed(conn, customers=50, orders=200)
    return conn


def test_export_writes_header_and_all_rows(small_db, tmp_path):
    out = tmp_path / "orders.csv"
    count = export_report.export_csv(small_db, str(out))
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == export_report.HEADER
    assert count == 200
    assert len(rows) == 201


def test_export_keeps_chinese_names(small_db, tmp_path):
    out = tmp_path / "orders.csv"
    export_report.export_csv(small_db, str(out))
    text = out.read_text(encoding="utf-8-sig")
    assert any("一" <= ch <= "鿿" for ch in text)


def test_amount_is_formatted_in_currency_units(small_db, tmp_path):
    out = tmp_path / "orders.csv"
    export_report.export_csv(small_db, str(out))
    with open(out, encoding="utf-8-sig", newline="") as f:
        first = list(csv.DictReader(f))[0]
    assert "." in first["amount"] and len(first["amount"].split(".")[1]) == 2


def test_order_numbers_are_unique():
    # Planted flaky test: make_order_numbers can produce duplicates.
    # FLOW_DEMO_FLAKY_RATE=1 forces a failure (used for the circuit-breaker drill).
    forced = float(os.environ.get("FLOW_DEMO_FLAKY_RATE", "0"))
    if forced and random.random() < forced:
        pytest.fail("forced failure for the circuit-breaker drill (FLOW_DEMO_FLAKY_RATE)")
    numbers = db.make_order_numbers(300, random.Random())
    assert len(numbers) == len(set(numbers))
