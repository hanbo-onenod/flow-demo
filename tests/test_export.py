from __future__ import annotations

import csv
import os
import random
from datetime import date

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


def _write_prechange_csv(conn, path: str) -> None:
    """Write the CSV the way export_csv did before the UTF-8 BOM."""
    rows = export_report.fetch_rows(conn)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(export_report.HEADER)
        writer.writerows(rows)


def test_export_starts_with_utf8_bom_and_rest_matches_prechange(tmp_path):
    """Default seed, 20,000 customers and 10,000 orders: BOM, then the old bytes."""
    conn = db.connect()
    db.seed(conn, customers=20000, orders=10000)
    out = tmp_path / "orders.csv"
    prechange = tmp_path / "orders_prechange.csv"
    export_report.export_csv(conn, str(out))
    _write_prechange_csv(conn, str(prechange))

    exported = out.read_bytes()
    before = prechange.read_bytes()
    assert exported[:3] == b"\xef\xbb\xbf"
    assert exported[3:] == before

    with open(out, encoding="utf-8-sig", newline="") as f:
        new_text = f.read()
    with open(prechange, encoding="utf-8", newline="") as f:
        old_text = f.read()
    assert new_text == old_text


def test_order_numbers_are_unique():
    # Planted flaky test: make_order_numbers can produce duplicates.
    # FLOW_DEMO_FLAKY_RATE=1 forces a failure (used for the circuit-breaker drill).
    forced = float(os.environ.get("FLOW_DEMO_FLAKY_RATE") or 0)
    if forced and random.random() < forced:
        pytest.fail("forced failure for the circuit-breaker drill (FLOW_DEMO_FLAKY_RATE)")
    numbers = db.make_order_numbers(300, random.Random())
    assert len(numbers) == len(set(numbers))


@pytest.fixture(scope="module")
def demo_db():
    """Default demo seed: 20,000 customers and 10,000 orders, 2026-01-01 to 2026-10-27."""
    conn = db.connect()
    db.seed(conn)
    return conn


def _order_numbers(conn, where: str, params: tuple = ()) -> list[int]:
    return [
        row[0]
        for row in conn.execute(
            f"SELECT order_no FROM orders WHERE {where} ORDER BY order_date, order_no",
            params,
        )
    ]


def _csv_order_numbers(path) -> list[int]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == export_report.HEADER
    return [int(row[0]) for row in rows[1:]]


def test_export_one_month_matches_sql(demo_db, tmp_path):
    out = tmp_path / "mar.csv"
    count = export_report.export_csv(demo_db, str(out), start="2026-03-01", end="2026-03-31")
    want = _order_numbers(demo_db, "order_date BETWEEN '2026-03-01' AND '2026-03-31'")
    got = _csv_order_numbers(out)
    assert want and got == want
    assert count == len(want)
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert all(row[1].startswith("2026-03") for row in rows[1:])


def test_cli_one_month_matches_sql(demo_db, tmp_path, capsys):
    out = tmp_path / "mar.csv"
    code = export_report.main([str(out), "--start", "2026-03-01", "--end", "2026-03-31"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    want = _order_numbers(demo_db, "order_date BETWEEN '2026-03-01' AND '2026-03-31'")
    got = _csv_order_numbers(out)
    assert want and got == want
    assert captured.out == f"exported {len(want)} rows to {out}\n"


def test_single_day_range_is_inclusive(demo_db, tmp_path):
    out = tmp_path / "day.csv"
    count = export_report.export_csv(demo_db, str(out), start="2026-03-15", end="2026-03-15")
    want = demo_db.execute(
        "SELECT COUNT(*) FROM orders WHERE order_date = '2026-03-15'"
    ).fetchone()[0]
    assert want > 0
    assert count == want
    got = _csv_order_numbers(out)
    assert len(got) == want
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert all(row[1] == "2026-03-15" for row in rows[1:])


def test_date_objects_are_accepted(demo_db, tmp_path):
    out = tmp_path / "day.csv"
    count = export_report.export_csv(
        demo_db, str(out), start=date(2026, 3, 15), end=date(2026, 3, 15)
    )
    want = demo_db.execute(
        "SELECT COUNT(*) FROM orders WHERE order_date = '2026-03-15'"
    ).fetchone()[0]
    assert want > 0 and count == want


def test_start_only_exports_from_that_day(demo_db, tmp_path):
    out = tmp_path / "from.csv"
    count = export_report.export_csv(demo_db, str(out), start="2026-10-01")
    want = _order_numbers(demo_db, "order_date >= '2026-10-01'")
    assert want and count == len(want)
    assert _csv_order_numbers(out) == want


def test_cli_start_only(demo_db, tmp_path, capsys):
    out = tmp_path / "from.csv"
    code = export_report.main([str(out), "--start", "2026-10-01"])
    assert code == 0
    capsys.readouterr()
    want = _order_numbers(demo_db, "order_date >= '2026-10-01'")
    assert want and _csv_order_numbers(out) == want


def test_end_only_exports_through_that_day(demo_db, tmp_path):
    out = tmp_path / "through.csv"
    count = export_report.export_csv(demo_db, str(out), end="2026-01-31")
    want = _order_numbers(demo_db, "order_date <= '2026-01-31'")
    assert want and count == len(want)
    assert _csv_order_numbers(out) == want


def test_cli_end_only(demo_db, tmp_path, capsys):
    out = tmp_path / "through.csv"
    code = export_report.main([str(out), "--end", "2026-01-31"])
    assert code == 0
    capsys.readouterr()
    want = _order_numbers(demo_db, "order_date <= '2026-01-31'")
    assert want and _csv_order_numbers(out) == want


def test_omitted_range_matches_unfiltered_export(demo_db, tmp_path):
    plain = tmp_path / "plain.csv"
    explicit = tmp_path / "explicit.csv"
    export_report.export_csv(demo_db, str(plain))
    export_report.export_csv(demo_db, str(explicit), start=None, end=None)
    assert plain.read_bytes() == explicit.read_bytes()
    want = [
        row[0]
        for row in demo_db.execute("SELECT order_no FROM orders ORDER BY order_date, order_no")
    ]
    assert _csv_order_numbers(plain) == want


def test_cli_without_range_matches_function(demo_db, tmp_path, capsys):
    via_fn = tmp_path / "fn.csv"
    via_cli = tmp_path / "cli.csv"
    export_report.export_csv(demo_db, str(via_fn))
    code = export_report.main([str(via_cli)])
    assert code == 0
    capsys.readouterr()
    assert via_fn.read_bytes() == via_cli.read_bytes()


_INVALID_DATES = ["2026/03/01", "2026-3-1", "2026-02-30", "abc"]


@pytest.mark.parametrize("flag", ["--start", "--end"])
@pytest.mark.parametrize("value", _INVALID_DATES)
def test_cli_rejects_invalid_date(tmp_path, capsys, flag, value):
    out = tmp_path / "bad.csv"
    out.write_bytes(b"keep-me")
    code = export_report.main([str(out), flag, value])
    err = capsys.readouterr().err
    assert code != 0
    assert err == f"invalid {flag} date {value}: expected YYYY-MM-DD\n"
    assert out.read_bytes() == b"keep-me"


@pytest.mark.parametrize("value", _INVALID_DATES)
def test_cli_invalid_date_does_not_create_file(tmp_path, capsys, value):
    out = tmp_path / "bad.csv"
    code = export_report.main([str(out), "--start", value])
    capsys.readouterr()
    assert code != 0
    assert not out.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "2026/03/01"},
        {"end": "2026/03/01"},
        {"start": "2026-3-1"},
        {"end": "2026-3-1"},
        {"start": "2026-02-30"},
        {"end": "2026-02-30"},
        {"start": "abc"},
        {"end": "abc"},
    ],
)
def test_function_rejects_invalid_date(demo_db, tmp_path, kwargs):
    out = tmp_path / "bad.csv"
    missing = tmp_path / "missing.csv"
    out.write_bytes(b"keep-me")
    name, bad = next(iter(kwargs.items()))
    with pytest.raises(ValueError) as exc:
        export_report.export_csv(demo_db, str(out), **kwargs)
    assert str(exc.value) == f"invalid {name} date {bad}: expected YYYY-MM-DD"
    assert out.read_bytes() == b"keep-me"
    with pytest.raises(ValueError):
        export_report.export_csv(demo_db, str(missing), **kwargs)
    assert not missing.exists()


@pytest.mark.parametrize(
    "start, end, flag, bad",
    [
        ("2026/03/01", "2026-03-31", "--start", "2026/03/01"),
        ("2026-03-01", "2026/03/31", "--end", "2026/03/31"),
        ("2026/03/01", "abc", "--start", "2026/03/01"),
        ("not-a-date", "2026-02-30", "--start", "not-a-date"),
    ],
)
def test_cli_names_the_invalid_option_when_both_are_given(
    tmp_path, capsys, start, end, flag, bad
):
    """With both bounds set, the message names the option that is wrong."""
    out = tmp_path / "bad.csv"
    out.write_bytes(b"keep-me")
    code = export_report.main([str(out), "--start", start, "--end", end])
    err = capsys.readouterr().err
    assert code != 0
    assert err == f"invalid {flag} date {bad}: expected YYYY-MM-DD\n"
    other = "--end" if flag == "--start" else "--start"
    assert other not in err
    assert out.read_bytes() == b"keep-me"


@pytest.mark.parametrize(
    "start, end, name, bad",
    [
        ("2026/03/01", "2026-03-31", "start", "2026/03/01"),
        ("2026-03-01", "2026/03/31", "end", "2026/03/31"),
        ("2026-02-30", "2026-3-1", "start", "2026-02-30"),
        ("2026-03-01", "abc", "end", "abc"),
    ],
)
def test_function_names_the_invalid_argument(demo_db, tmp_path, start, end, name, bad):
    out = tmp_path / "bad.csv"
    missing = tmp_path / "missing.csv"
    out.write_bytes(b"keep-me")
    with pytest.raises(ValueError) as exc:
        export_report.export_csv(demo_db, str(out), start=start, end=end)
    assert str(exc.value) == f"invalid {name} date {bad}: expected YYYY-MM-DD"
    assert out.read_bytes() == b"keep-me"
    with pytest.raises(ValueError) as missing_exc:
        export_report.export_csv(demo_db, str(missing), start=start, end=end)
    assert str(missing_exc.value) == f"invalid {name} date {bad}: expected YYYY-MM-DD"
    assert not missing.exists()


def test_start_after_end_is_an_error(demo_db, tmp_path, capsys):
    out = tmp_path / "bad.csv"
    out.write_bytes(b"keep-me")
    code = export_report.main(
        [str(out), "--start", "2026-04-01", "--end", "2026-03-01"]
    )
    err = capsys.readouterr().err
    assert code != 0
    assert "the start date is after the end date" in err
    assert out.read_bytes() == b"keep-me"

    missing = tmp_path / "missing.csv"
    code = export_report.main(
        [str(missing), "--start", "2026-04-01", "--end", "2026-03-01"]
    )
    capsys.readouterr()
    assert code != 0
    assert not missing.exists()

    with pytest.raises(ValueError, match="the start date is after the end date"):
        export_report.export_csv(demo_db, str(out), start="2026-04-01", end="2026-03-01")
    assert out.read_bytes() == b"keep-me"


def test_empty_range_writes_header_only(demo_db, tmp_path, capsys):
    out = tmp_path / "empty.csv"
    code = export_report.main([str(out), "--start", "2027-01-01", "--end", "2027-01-31"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == f"exported 0 rows to {out}\n"
    assert captured.err == ""
    assert out.read_bytes() == b"\xef\xbb\xbf" + b"order_no,order_date,customer,city,amount\r\n"

    other = tmp_path / "empty-fn.csv"
    count = export_report.export_csv(demo_db, str(other), start="2027-01-01", end="2027-01-31")
    assert count == 0
    assert other.read_bytes() == out.read_bytes()
