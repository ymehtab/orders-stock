import psycopg

from orders_stock.db import Database
from orders_stock.models import ProductStockResponse


def get_stock(db: Database, sku: str) -> ProductStockResponse | None:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT sku, stock
            FROM products
            WHERE sku = %s
            """,
            (sku,),
        ).fetchone()
    return ProductStockResponse(**row) if row else None


def process_next_job(db: Database) -> str | None:
    """
    Apply one available stock job and return its order ID.

    Skip jobs locked by other workers. Return None if no pending job can be
    claimed. Commit all deductions and the completion marker together, or
    roll them all back if processing fails.

    Raises:
        psycopg.Error: If database processing fails.
    """
    with db.connect() as conn:
        job = conn.execute(
            """
            SELECT stock_jobs.order_id
            FROM stock_jobs
            JOIN orders USING (order_id)
            WHERE stock_jobs.processed_at IS NULL
            ORDER BY orders.accepted_at, stock_jobs.order_id
            LIMIT 1
            FOR UPDATE OF stock_jobs SKIP LOCKED
            """
        ).fetchone()
        if job is None:
            return None
        _deduct_order_stock(conn, job["order_id"])
        conn.execute(
            """
            UPDATE stock_jobs
            SET processed_at = clock_timestamp()
            WHERE order_id = %s
            """,
            (job["order_id"],),
        )
    return job["order_id"]


def _deduct_order_stock(conn: psycopg.Connection, order_id: str) -> None:
    items = conn.execute(
        """
        SELECT sku, quantity
        FROM order_items
        WHERE order_id = %s
        ORDER BY sku
        """,
        (order_id,),
    ).fetchall()
    # PostgreSQL holds each updated product's lock until the transaction ends.
    # Update products in SKU order so workers cannot each hold a product lock
    # that the other is waiting for (a deadlock).
    for item in items:
        conn.execute(
            """
            UPDATE products
            SET stock = stock - %s
            WHERE sku = %s
            """,
            (item["quantity"], item["sku"]),
        )
