from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderItemRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0, le=2_147_483_647, strict=True)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    items: list[OrderItemRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_skus(self) -> Self:
        """Reject repeated SKUs."""
        skus = [item.sku for item in self.items]
        if len(skus) != len(set(skus)):
            raise ValueError("Each SKU must appear only once per order")
        return self


class OrderItemResponse(BaseModel):
    sku: str
    quantity: int
    unit_price_cents: int


class OrderResponse(BaseModel):
    order_id: str
    customer_id: str
    items: list[OrderItemResponse]
    total_cents: int
    accepted_at: datetime
    stock_status: Literal["PENDING", "APPLIED"]


class ProductStockResponse(BaseModel):
    sku: str
    stock: int


class ProductReportItem(BaseModel):
    sku: str
    units_sold: int
    current_stock: int


class DailyReportResponse(BaseModel):
    date: date
    timezone: Literal["UTC"] = "UTC"
    total_orders: int
    revenue_cents: int
    products: list[ProductReportItem]
