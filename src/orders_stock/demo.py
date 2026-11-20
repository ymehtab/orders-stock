"""Demonstrate duplicates and worker recovery through an already-running API."""

import argparse
import math
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx2


def wait_applied(
    client: httpx2.Client, order_id: str, timeout_seconds: float
) -> dict[str, Any]:
    """
    Return an order once its stock update is applied, or time out.

    Use timeout_seconds as the polling budget, in seconds, for this order.
    Each HTTP request uses the client's timeout and may finish after the
    polling deadline.

    Raises:
        TimeoutError: If polling ends without an applied stock update.
        httpx2.HTTPError: If an order request fails.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        order = get_json(client, f"/orders/{order_id}")
        if order["stock_status"] == "APPLIED":
            return order
        time.sleep(0.05)
    raise TimeoutError(f"Stock processing did not finish for {order_id}")


def check(
    label: str, expected: object, actual: object, *, show_success: bool = True
) -> None:
    if actual != expected:
        raise AssertionError(f"FAIL {label}: expected={expected!r}, actual={actual!r}")
    if show_success:
        print(f"PASS {label}: {actual!r}")


def get_json(client: httpx2.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def check_report(
    client: httpx2.Client,
    report_path: str,
    baseline_report: dict[str, Any],
    order_count: int,
    revenue_cents: int,
    bananas_sold: int,
    apples_sold: int,
) -> None:
    """Check report increases relative to the starting totals."""
    report = get_json(client, report_path)
    check(
        "Daily orders",
        expected=baseline_report["total_orders"] + order_count,
        actual=report["total_orders"],
    )
    check(
        "Daily revenue (cents)",
        expected=baseline_report["revenue_cents"] + revenue_cents,
        actual=report["revenue_cents"],
    )
    baseline_units_sold_by_sku = {
        product["sku"]: product["units_sold"] for product in baseline_report["products"]
    }
    units_sold_by_sku = {
        product["sku"]: product["units_sold"] for product in report["products"]
    }
    for sku, quantity in [("BAN-001", bananas_sold), ("APL-003", apples_sold)]:
        check(
            f"{sku} units sold",
            expected=baseline_units_sold_by_sku[sku] + quantity,
            actual=units_sold_by_sku[sku],
        )


def check_stock(
    client: httpx2.Client,
    baseline_products_by_sku: dict[str, dict[str, Any]],
    bananas_deducted: int,
    apples_deducted: int,
) -> None:
    """Check stock against starting levels minus the supplied quantities."""
    for sku, quantity in [
        ("BAN-001", bananas_deducted),
        ("APL-003", apples_deducted),
    ]:
        check(
            f"{sku} stock",
            expected=baseline_products_by_sku[sku]["current_stock"] - quantity,
            actual=get_json(client, f"/products/{sku}/stock")["stock"],
        )


def run_demo(client: httpx2.Client, timeout_seconds: float) -> None:
    """
    Verify duplicate submissions and recovery after a worker interruption.

    Save two orders through the API. Compare report and stock changes with
    starting values, and prompt for manual worker stop/restart. Apply
    timeout_seconds, in seconds, to each wait for a stock update.
    """
    day = datetime.now(UTC).date().isoformat()
    report_path = f"/reports/daily?date={day}"
    # Compare with starting totals so earlier demo runs are accounted for.
    baseline_report = get_json(client, report_path)
    baseline_products_by_sku = {
        product["sku"]: product for product in baseline_report["products"]
    }
    order_id_prefix = f"demo-{uuid4().hex[:12]}"

    print("\n" + "-" * 60)
    print("Scenario 1: duplicate submissions")
    print("-" * 60)
    payload = {
        "order_id": f"{order_id_prefix}-1",
        "customer_id": "cust-42",
        "items": [
            {"sku": "BAN-001", "quantity": 2},
            {"sku": "APL-003", "quantity": 1},
        ],
    }
    first = client.post("/orders", json=payload)
    check("New order HTTP status", expected=201, actual=first.status_code)
    order = first.json()
    check("Order total in cents", expected=697, actual=order["total_cents"])
    duplicate_count = 5
    for _ in range(duplicate_count):
        duplicate = client.post("/orders", json=payload)
        check(
            "Duplicate HTTP status",
            expected=200,
            actual=duplicate.status_code,
            show_success=False,
        )
        check(
            "Duplicate order ID",
            expected=order["order_id"],
            actual=duplicate.json()["order_id"],
            show_success=False,
        )
    print(
        f"PASS Duplicate submissions: {duplicate_count} returned HTTP 200 "
        "and the original order ID"
    )
    applied_order = wait_applied(client, payload["order_id"], timeout_seconds)
    check(
        "Stock status after processing",
        expected="APPLIED",
        actual=applied_order["stock_status"],
    )
    check_stock(
        client,
        baseline_products_by_sku,
        bananas_deducted=2,
        apples_deducted=1,
    )
    check_report(
        client,
        report_path,
        baseline_report,
        order_count=1,
        revenue_cents=697,
        bananas_sold=2,
        apples_sold=1,
    )

    print("\n" + "-" * 60)
    print("Scenario 2: worker interruption and catch-up")
    print("-" * 60)
    input("Stop the worker with Ctrl-C in its terminal, then press Enter here: ")
    print("\nWhile the worker is stopped:")
    payload = {
        "order_id": f"{order_id_prefix}-2",
        "customer_id": "cust-42",
        "items": [{"sku": "BAN-001", "quantity": 1}],
    }
    second = client.post("/orders", json=payload)
    check(
        "Order accepted while worker is stopped",
        expected=201,
        actual=second.status_code,
    )
    second_order = second.json()
    check(
        "Stock status while stopped",
        expected="PENDING",
        actual=second_order["stock_status"],
    )
    # The second order counts as a sale, but its stock update is still pending.
    # Only the first order's 2 bananas and 1 apple have been deducted.
    check_stock(
        client,
        baseline_products_by_sku,
        bananas_deducted=2,
        apples_deducted=1,
    )
    check_report(
        client,
        report_path,
        baseline_report,
        order_count=2,
        revenue_cents=896,
        bananas_sold=3,
        apples_sold=1,
    )

    input("Restart the worker with 'make run-worker', then press Enter here: ")
    print("\nAfter restarting the worker:")
    applied_order = wait_applied(client, payload["order_id"], timeout_seconds)
    check(
        "Stock status after restart",
        expected="APPLIED",
        actual=applied_order["stock_status"],
    )
    check_stock(
        client,
        baseline_products_by_sku,
        bananas_deducted=3,
        apples_deducted=1,
    )
    check_report(
        client,
        report_path,
        baseline_report,
        order_count=2,
        revenue_cents=896,
        bananas_sold=3,
        apples_sold=1,
    )
    print("\nBoth scenarios passed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demonstrate duplicate submissions and worker recovery."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=15,
        help="Seconds to wait for each stock update (default: %(default)s)",
    )
    args = parser.parse_args()
    if not 0 < args.timeout_seconds < math.inf:
        parser.error("--timeout-seconds must be a finite, positive number")
    with httpx2.Client(base_url=args.base_url, timeout=5, trust_env=False) as client:
        run_demo(client, args.timeout_seconds)


if __name__ == "__main__":
    main()
