from datetime import UTC, date, datetime, time, timedelta

from psycopg import IsolationLevel

from orders_stock.db import Database
from orders_stock.models import DailyReportResponse


def daily_report(db: Database, day: date) -> DailyReportResponse:
    """
    Return accepted-order totals for a UTC day and current stock per SKU.

    Include orders from midnight on the given day up to, but not including,
    the next midnight. Both queries read the same committed data. Stock
    reflects completed updates at report time, even for a past day.
    """
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    with db.connect() as conn:
        # Keep both queries on the same view of committed data.
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        totals = conn.execute(
            """
            SELECT count(*) AS total_orders, COALESCE(sum(total_cents), 0) AS revenue_cents
            FROM orders
            WHERE accepted_at >= %s AND accepted_at < %s
            """,
            (start, end),
        ).fetchone()
        products = conn.execute(
            """
            SELECT products.sku, COALESCE(sold.units_sold, 0) AS units_sold,
                   products.stock AS current_stock
            FROM products
            LEFT JOIN (
                SELECT order_items.sku, sum(order_items.quantity) AS units_sold
                FROM order_items
                JOIN orders USING (order_id)
                WHERE orders.accepted_at >= %s AND orders.accepted_at < %s
                GROUP BY order_items.sku
            ) sold ON sold.sku = products.sku
            ORDER BY products.sku
            """,
            (start, end),
        ).fetchall()
    return DailyReportResponse(date=day, **totals, products=products)
