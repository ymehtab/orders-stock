import pytest
from pydantic import ValidationError

from orders_stock.models import CreateOrderRequest


def make_order(**changes):
    return CreateOrderRequest.model_validate(
        {
            "order_id": "web-1",
            "customer_id": "cust-1",
            "items": [{"sku": "BAN-001", "quantity": 1}],
        }
        | changes
    )


def test_trims_identifiers():
    order = make_order(
        order_id=" web-1 ",
        customer_id=" cust-1 ",
        items=[{"sku": " BAN-001 ", "quantity": 1}],
    )

    assert order.order_id == "web-1"
    assert order.customer_id == "cust-1"
    assert order.items[0].sku == "BAN-001"


def test_preserves_sku_case():
    order = make_order(
        items=[
            {"sku": "BAN-001", "quantity": 2},
            {"sku": "ban-001", "quantity": 3},
        ]
    )

    assert [item.sku for item in order.items] == ["BAN-001", "ban-001"]


@pytest.mark.parametrize("sku", ["BAN-001", " BAN-001 "], ids=["exact", "trimmed"])
def test_rejects_repeated_skus(sku):
    with pytest.raises(ValidationError):
        make_order(
            items=[
                {"sku": "BAN-001", "quantity": 2},
                {"sku": "APL-003", "quantity": 1},
                {"sku": sku, "quantity": 3},
            ]
        )


def test_accepts_quantity_at_database_limit():
    order = make_order(items=[{"sku": "BAN-001", "quantity": 2_147_483_647}])

    assert order.items[0].quantity == 2_147_483_647


@pytest.mark.parametrize(
    "quantity",
    [0, -1, 1.5, True, "2", 2_147_483_648],
    ids=["zero", "negative", "fraction", "boolean", "numeric-string", "overflow"],
)
def test_rejects_invalid_quantities(quantity):
    with pytest.raises(ValidationError):
        make_order(items=[{"sku": "BAN-001", "quantity": quantity}])


@pytest.mark.parametrize(
    "changes",
    [
        {"order_id": "   "},
        {"customer_id": ""},
        {"items": []},
        {"items": [{"sku": "   ", "quantity": 1}]},
    ],
    ids=["blank-order-id", "blank-customer", "empty-order", "blank-sku"],
)
def test_rejects_empty_order_details(changes):
    with pytest.raises(ValidationError):
        make_order(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"total_cents": 1},
        {"items": [{"sku": "BAN-001", "quantity": 1, "unit_price_cents": 1}]},
    ],
    ids=["order-total", "item-price"],
)
def test_rejects_client_supplied_prices(changes):
    with pytest.raises(ValidationError):
        make_order(**changes)
