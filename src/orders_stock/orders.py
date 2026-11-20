from typing import NamedTuple

import psycopg

from orders_stock.db import Database
from orders_stock.models import CreateOrderRequest, OrderResponse


class CreateOrderResult(NamedTuple):
    """An order and whether it was created by the current request."""

    order: OrderResponse
    created: bool


class UnknownProductsError(ValueError):
    """Raised when a new order references unknown product SKUs."""


def get_order(db: Database, order_id: str) -> OrderResponse | None:
    with db.connect() as conn:
        return _read_order(conn, order_id)


def create_order(db: Database, request: CreateOrderRequest) -> CreateOrderResult:
    """
    Save an order and its stock job, or return the existing order for its ID.

    The result's created flag is False for an existing order ID.
    New orders, priced items, and stock jobs commit together. Existing orders
    are returned unchanged without adding items or another job.

    Raises:
        UnknownProductsError: If an order being created contains unknown SKUs.
    """
    with db.connect() as conn:
        existing = _read_order(conn, request.order_id)
        if existing is not None:
            return CreateOrderResult(order=existing, created=False)

        skus = [item.sku for item in request.items]
        price_cents_by_sku = _get_product_prices(conn, skus)
        total_cents = sum(
            item.quantity * price_cents_by_sku[item.sku] for item in request.items
        )
        insert_result = conn.execute(
            """
            INSERT INTO orders (order_id, customer_id, total_cents)
            VALUES (%s, %s, %s)
            ON CONFLICT (order_id) DO NOTHING
            """,
            (request.order_id, request.customer_id, total_cents),
        )
        if insert_result.rowcount == 0:
            # Another request saved this order after our first lookup.
            # ON CONFLICT skipped our insert. READ COMMITTED lets the next
            # lookup see the saved order, its items, and its stock job.
            return CreateOrderResult(
                order=_read_order(conn, request.order_id), created=False
            )

        _insert_order_items(conn, request, price_cents_by_sku)
        conn.execute(
            """
            INSERT INTO stock_jobs (order_id)
            VALUES (%s)
            """,
            (request.order_id,),
        )
        order = _read_order(conn, request.order_id)
    return CreateOrderResult(order=order, created=True)


def _get_product_prices(conn: psycopg.Connection, skus: list[str]) -> dict[str, int]:
    """
    Return current prices in cents for every requested SKU.

    Raises:
        UnknownProductsError: If any requested SKU is missing.
    """
    products = conn.execute(
        """
        SELECT sku, price_cents
        FROM products
        WHERE sku = ANY(%s)
        """,
        (skus,),
    ).fetchall()
    price_cents_by_sku = {
        product["sku"]: product["price_cents"] for product in products
    }
    missing_skus = sorted(set(skus) - price_cents_by_sku.keys())
    if missing_skus:
        raise UnknownProductsError(f"Unknown SKUs: {', '.join(missing_skus)}")
    return price_cents_by_sku


def _insert_order_items(
    conn: psycopg.Connection,
    request: CreateOrderRequest,
    price_cents_by_sku: dict[str, int],
) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO order_items (order_id, sku, quantity, unit_price_cents)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (
                    request.order_id,
                    item.sku,
                    item.quantity,
                    price_cents_by_sku[item.sku],
                )
                for item in request.items
            ],
        )


def _read_order(conn: psycopg.Connection, order_id: str) -> OrderResponse | None:
    row = conn.execute(
        """
        SELECT orders.order_id, orders.customer_id,
               orders.total_cents, orders.accepted_at,
               CASE WHEN stock_jobs.processed_at IS NULL THEN 'PENDING' ELSE 'APPLIED' END
                   AS stock_status
        FROM orders
        JOIN stock_jobs USING (order_id)
        WHERE orders.order_id = %s
        """,
        (order_id,),
    ).fetchone()
    if row is None:
        return None
    items = conn.execute(
        """
        SELECT sku, quantity, unit_price_cents
        FROM order_items
        WHERE order_id = %s
        ORDER BY sku
        """,
        (order_id,),
    ).fetchall()
    return OrderResponse(**row, items=items)
