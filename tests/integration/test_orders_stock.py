import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import Cursor, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from orders_stock import orders
from orders_stock.api import create_app
from orders_stock.db import Database, main
from orders_stock.models import CreateOrderRequest
from orders_stock.orders import create_order, get_order
from orders_stock.reports import daily_report
from orders_stock.stock import get_stock, process_next_job


@pytest.fixture
def db():
    # A separate schema per test keeps local application data intact and also
    # lets concurrent test runs share one dedicated development database.
    base_db = Database(
        os.environ.get("TEST_DATABASE_URL", Database.from_env().database_url)
    )
    schema = f"test_{uuid4().hex}"
    with base_db.connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    options = conninfo_to_dict(base_db.database_url).get("options", "")
    database = Database(
        make_conninfo(
            base_db.database_url, options=f"{options} -c search_path={schema}"
        )
    )
    try:
        database.initialize()
        database.seed_products()
        yield database
    finally:
        with base_db.connect() as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as test_client:
        yield test_client


@pytest.fixture
def start_worker(db):
    workers = []

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "orders_stock.worker",
                "--poll-interval-seconds",
                "0.02",
            ],
            env={**os.environ, "DATABASE_URL": db.database_url},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        workers.append(process)
        return process

    yield start
    for process in workers:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def order_payload(order_id="web-100045", items=None):
    if items is None:
        items = [
            {"sku": "BAN-001", "quantity": 2},
            {"sku": "APL-003", "quantity": 1},
        ]
    return {
        "order_id": order_id,
        "customer_id": "cust-42",
        "items": items,
    }


def table_counts(db):
    with db.connect() as conn:
        return conn.execute(
            """
            SELECT
                (SELECT count(*) FROM orders) AS orders,
                (SELECT count(*) FROM order_items) AS items,
                (SELECT count(*) FROM stock_jobs) AS jobs
            """
        ).fetchone()


def wait_until(condition, timeout_seconds=10):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    pytest.fail("Expected state was not reached before the timeout")


def test_order_saves_prices_customer_total_and_pending_job(client, db):
    response = client.post("/orders", json=order_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == "web-100045"
    assert body["customer_id"] == "cust-42"
    assert body["total_cents"] == 697
    assert body["stock_status"] == "PENDING"
    assert datetime.fromisoformat(body["accepted_at"]).utcoffset() == timedelta(0)
    assert body["items"] == [
        {"sku": "APL-003", "quantity": 1, "unit_price_cents": 299},
        {"sku": "BAN-001", "quantity": 2, "unit_price_cents": 199},
    ]
    assert client.get("/orders/web-100045").json() == body
    assert table_counts(db) == {"orders": 1, "items": 2, "jobs": 1}
    assert client.get("/products/BAN-001/stock").json() == {
        "sku": "BAN-001",
        "stock": 50,
    }


def test_concurrent_duplicates_create_one_order_and_one_deduction(
    client, db, monkeypatch
):
    submission_count = 8
    barrier = Barrier(submission_count)
    original_read = orders._read_order

    def synchronized_read(conn, order_id):
        result = original_read(conn, order_id)
        if result is None:
            # Force every initial lookup to miss so the INSERT constraint, not
            # just the early lookup, must resolve simultaneous submissions.
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(orders, "_read_order", synchronized_read)
    with ThreadPoolExecutor(max_workers=submission_count) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/orders", json=order_payload()),
                range(submission_count),
            )
        )
    status_codes = sorted(response.status_code for response in responses)
    assert status_codes == [200] * (submission_count - 1) + [201]
    assert all(r.json() == responses[0].json() for r in responses)
    assert table_counts(db) == {"orders": 1, "items": 2, "jobs": 1}
    assert process_next_job(db) == "web-100045"
    assert process_next_job(db) is None
    assert get_stock(db, "BAN-001").stock == 48
    assert get_stock(db, "APL-003").stock == 29
    assert client.get("/orders/web-100045").json()["stock_status"] == "APPLIED"


def test_order_id_with_slashes_can_be_fetched_and_processed(client, db):
    created = client.post("/orders", json=order_payload("web/mobile/100045"))
    assert created.status_code == 201

    fetched = client.get("/orders/web%2Fmobile%2F100045")

    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert process_next_job(db) == "web/mobile/100045"
    assert get_stock(db, "BAN-001").stock == 48


def test_stock_sku_with_slashes_can_be_fetched(client, db):
    with db.connect() as conn:
        conn.execute("INSERT INTO products VALUES ('BOX/RED', 'Red box', 99, 10)")

    response = client.get("/products/BOX%2FRED/stock")

    assert response.status_code == 200
    assert response.json() == {"sku": "BOX/RED", "stock": 10}


def test_duplicate_returns_original_body_even_after_catalogue_price_change(client, db):
    response = client.post("/orders", json=order_payload())
    assert response.status_code == 201
    original_order = response.json()

    with db.connect() as conn:
        conn.execute("UPDATE products SET price_cents = 9999")
    changed_payload = order_payload(items=[{"sku": "UNKNOWN", "quantity": 5}])
    changed_payload["customer_id"] = "different-customer"
    response = client.post("/orders", json=changed_payload)
    assert response.status_code == 200
    assert response.json() == original_order
    day = date.fromisoformat(original_order["accepted_at"][:10])
    assert daily_report(db, day).revenue_cents == 697

    response = client.post("/orders", json=order_payload("new-price"))
    assert response.status_code == 201
    new_order = response.json()
    assert new_order["total_cents"] == 3 * 9999


def test_worker_stop_and_restart_catches_up_without_repeating_stock(
    client, db, start_worker
):
    client.post("/orders", json=order_payload()).raise_for_status()
    worker = start_worker()
    wait_until(lambda: get_order(db, "web-100045").stock_status == "APPLIED")
    worker.terminate()
    worker.wait(timeout=5)
    second = client.post(
        "/orders", json=order_payload("web-100046", [{"sku": "BAN-001", "quantity": 1}])
    )
    assert second.status_code == 201
    assert second.json()["stock_status"] == "PENDING"
    assert get_stock(db, "BAN-001").stock == 48
    # Keep both orders on one UTC day even if the test crosses midnight.
    accepted_at = datetime(2026, 11, 18, 12, tzinfo=UTC)
    with db.connect() as conn:
        conn.execute("UPDATE orders SET accepted_at = %s", (accepted_at,))
    day = accepted_at.date()
    before = daily_report(db, day)
    assert before.total_orders == 2
    assert before.revenue_cents == 896
    start_worker()
    wait_until(lambda: get_order(db, "web-100046").stock_status == "APPLIED")
    assert get_stock(db, "BAN-001").stock == 47
    assert get_stock(db, "APL-003").stock == 29
    assert process_next_job(db) is None
    assert table_counts(db) == {"orders": 2, "items": 3, "jobs": 2}
    assert daily_report(db, day).revenue_cents == 896


def test_concurrent_workers_do_not_lose_or_repeat_updates(db):
    for index in range(12):
        create_order(
            db, CreateOrderRequest.model_validate(order_payload(f"order-{index}"))
        )

    def drain():
        while process_next_job(db) is not None:
            pass

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: drain(), range(4)))
    assert get_stock(db, "BAN-001").stock == 26
    assert get_stock(db, "APL-003").stock == 18
    with db.connect() as conn:
        pending_jobs = conn.execute(
            """
            SELECT count(*) AS pending
            FROM stock_jobs
            WHERE processed_at IS NULL
            """
        ).fetchone()["pending"]
    assert pending_jobs == 0


def test_worker_prefers_older_orders_over_id_order(db):
    for order_id in ["a-newer", "z-older"]:
        create_order(db, CreateOrderRequest.model_validate(order_payload(order_id)))
    with db.connect() as conn:
        conn.execute(
            "UPDATE orders SET accepted_at = '2000-01-01T00:00:00Z' "
            "WHERE order_id = 'z-older'"
        )

    assert process_next_job(db) == "z-older"
    assert process_next_job(db) == "a-newer"
    assert process_next_job(db) is None


def test_stock_transaction_rolls_back_all_items_and_can_be_retried(db):
    create_order(db, CreateOrderRequest.model_validate(order_payload()))
    with db.connect() as conn:
        conn.execute("""
            CREATE FUNCTION interrupt_stock() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.sku = 'BAN-001' THEN RAISE EXCEPTION 'test interruption'; END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER interrupt_stock BEFORE UPDATE ON products
            FOR EACH ROW EXECUTE FUNCTION interrupt_stock();
        """)
    # APL-003 is updated before BAN-001; the first deduction must roll back too.
    with pytest.raises(psycopg.errors.RaiseException):
        process_next_job(db)
    assert get_stock(db, "APL-003").stock == 30
    assert get_stock(db, "BAN-001").stock == 50
    assert get_order(db, "web-100045").stock_status == "PENDING"
    with db.connect() as conn:
        conn.execute("DROP TRIGGER interrupt_stock ON products")
    assert process_next_job(db) is not None
    assert get_stock(db, "APL-003").stock == 29
    assert get_stock(db, "BAN-001").stock == 48
    assert process_next_job(db) is None


def test_job_insert_failure_does_not_leave_a_partial_order(db):
    with db.connect() as conn:
        conn.execute("""
            CREATE FUNCTION reject_job() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test job failure'; END $$;
            CREATE TRIGGER reject_job BEFORE INSERT ON stock_jobs
            FOR EACH ROW EXECUTE FUNCTION reject_job();
        """)
    with pytest.raises(psycopg.errors.RaiseException):
        create_order(db, CreateOrderRequest.model_validate(order_payload()))
    assert table_counts(db) == {"orders": 0, "items": 0, "jobs": 0}


def test_report_utc_boundaries_revenue_and_current_stock(client, db):
    for order_id, timestamp, items in [
        ("before", "2026-11-17T23:59:59.999999Z", [{"sku": "BAN-001", "quantity": 10}]),
        ("start", "2026-11-18T00:00:00Z", None),
        ("end", "2026-11-18T23:59:59.999999Z", [{"sku": "BAN-001", "quantity": 1}]),
        ("after", "2026-11-19T00:00:00Z", [{"sku": "APL-003", "quantity": 4}]),
    ]:
        create_order(
            db, CreateOrderRequest.model_validate(order_payload(order_id, items))
        )
        with db.connect() as conn:
            conn.execute(
                "UPDATE orders SET accepted_at = %s WHERE order_id = %s",
                (datetime.fromisoformat(timestamp), order_id),
            )
    with db.connect() as conn:
        conn.execute("INSERT INTO products VALUES ('ZERO', 'No sales', 100, 9)")
    while process_next_job(db) is not None:
        pass
    response = client.get("/reports/daily?date=2026-11-18")
    assert response.status_code == 200
    assert response.json() == {
        "date": "2026-11-18",
        "timezone": "UTC",
        "total_orders": 2,
        "revenue_cents": 896,
        "products": [
            {"sku": "APL-003", "units_sold": 1, "current_stock": 25},
            {"sku": "BAN-001", "units_sold": 3, "current_stock": 37},
            {"sku": "ZERO", "units_sold": 0, "current_stock": 9},
        ],
    }


def test_empty_report_includes_unsold_products_and_zero_totals(client):
    report = client.get("/reports/daily?date=2000-01-01").json()
    assert report["total_orders"] == 0
    assert report["revenue_cents"] == 0
    assert report["products"] == [
        {"sku": "APL-003", "units_sold": 0, "current_stock": 30},
        {"sku": "BAN-001", "units_sold": 0, "current_stock": 50},
    ]


def test_report_remains_consistent_during_concurrent_updates(db):
    day = date(2026, 11, 18)
    write_injected = False

    class ConcurrentWriteCursor(Cursor):
        def fetchone(self):
            nonlocal write_injected
            result = super().fetchone()
            if not write_injected:
                write_injected = True
                # Commit one order after the report has read its totals.
                create_order(db, CreateOrderRequest.model_validate(order_payload()))
                with db.connect() as conn:
                    conn.execute("UPDATE orders SET accepted_at = %s", (day,))
                process_next_job(db)
            return result

    class ReportDatabase(Database):
        def connect(self):
            conn = super().connect()
            conn.cursor_factory = ConcurrentWriteCursor
            return conn

    report = daily_report(ReportDatabase(db.database_url), day)
    assert report.total_orders == 0
    assert report.revenue_cents == 0
    assert [(p.units_sold, p.current_stock) for p in report.products] == [
        (0, 30),
        (0, 50),
    ]
    next_report = daily_report(db, day)
    assert next_report.total_orders == 1
    assert next_report.revenue_cents == 697
    assert [(p.units_sold, p.current_stock) for p in next_report.products] == [
        (1, 29),
        (2, 48),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"items": []},
        {"customer_id": None},
        {"items": [{"sku": "BAN-001", "quantity": 0}]},
        {
            "items": [
                {"sku": "BAN-001", "quantity": 2},
                {"sku": " BAN-001 ", "quantity": 1},
            ]
        },
        {"items": [{"sku": "UNKNOWN", "quantity": 1}]},
    ],
    ids=[
        "empty-items",
        "null-customer",
        "zero-quantity",
        "duplicate-sku",
        "unknown-sku",
    ],
)
def test_invalid_orders_are_rejected_without_saving(client, db, change):
    response = client.post("/orders", json=order_payload() | change)
    assert response.status_code == 422
    assert table_counts(db) == {"orders": 0, "items": 0, "jobs": 0}


def test_missing_customer_is_rejected(client):
    request = order_payload()
    del request["customer_id"]
    assert client.post("/orders", json=request).status_code == 422


def test_unknown_resources_and_invalid_report_dates(client):
    assert client.get("/orders/missing").status_code == 404
    assert client.get("/products/missing/stock").status_code == 404
    assert client.get("/reports/daily").status_code == 422
    assert client.get("/reports/daily?date=not-a-date").status_code == 422


def test_seed_is_safe_to_repeat_after_stock_processing(db):
    create_order(db, CreateOrderRequest.model_validate(order_payload()))
    process_next_job(db)
    db.seed_products()
    assert get_stock(db, "BAN-001").stock == 48
    assert get_stock(db, "APL-003").stock == 29


def test_reset_command_rebuilds_tables_and_restores_sample_products(db, monkeypatch):
    create_order(db, CreateOrderRequest.model_validate(order_payload()))
    process_next_job(db)
    with db.connect() as conn:
        conn.execute("ALTER TABLE products ADD COLUMN obsolete TEXT")
        conn.execute("UPDATE products SET price_cents = 999")
        conn.execute(
            "INSERT INTO products (sku, name, price_cents, stock) "
            "VALUES ('EXTRA', 'Extra product', 100, 10)"
        )
        conn.execute("CREATE TABLE unrelated (value INTEGER)")
        conn.execute("INSERT INTO unrelated VALUES (42)")

    monkeypatch.setenv("DATABASE_URL", db.database_url)
    monkeypatch.setattr("sys.argv", ["orders-db", "reset"])
    main()

    assert table_counts(db) == {"orders": 0, "items": 0, "jobs": 0}
    with db.connect() as conn:
        assert conn.execute("SELECT * FROM products ORDER BY sku").fetchall() == [
            {"sku": "APL-003", "name": "Apples", "price_cents": 299, "stock": 30},
            {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 50},
        ]
        assert conn.execute("SELECT value FROM unrelated").fetchone() == {"value": 42}

    create_order(db, CreateOrderRequest.model_validate(order_payload()))
    assert process_next_job(db) == "web-100045"
    assert get_stock(db, "BAN-001").stock == 48


def test_reset_rolls_back_when_an_unexpected_dependency_blocks_a_drop(db):
    create_order(db, CreateOrderRequest.model_validate(order_payload()))
    with db.connect() as conn:
        conn.execute("CREATE VIEW product_names AS SELECT sku, name FROM products")

    with pytest.raises(psycopg.errors.DependentObjectsStillExist):
        db.reset()

    assert table_counts(db) == {"orders": 1, "items": 2, "jobs": 1}
    assert get_order(db, "web-100045").total_cents == 697
