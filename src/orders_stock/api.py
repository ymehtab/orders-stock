import datetime

from fastapi import FastAPI, HTTPException, Response

from orders_stock.db import Database
from orders_stock.models import (
    CreateOrderRequest,
    DailyReportResponse,
    OrderResponse,
    ProductStockResponse,
)
from orders_stock.orders import UnknownProductsError, create_order, get_order
from orders_stock.reports import daily_report
from orders_stock.stock import get_stock


def create_app(db: Database | None = None) -> FastAPI:
    database = db or Database.from_env()
    app = FastAPI(title="Orders and Stock", version="0.1.0")

    @app.post(
        "/orders",
        status_code=201,
        responses={200: {"model": OrderResponse}},
    )
    def post_order(request: CreateOrderRequest, response: Response) -> OrderResponse:
        try:
            result = create_order(database, request)
            response.status_code = 201 if result.created else 200
            return result.order
        except UnknownProductsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/orders/{order_id:path}")
    def read_order(order_id: str) -> OrderResponse:
        order = get_order(database, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    @app.get("/products/{sku:path}/stock")
    def read_stock(sku: str) -> ProductStockResponse:
        stock = get_stock(database, sku)
        if stock is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return stock

    @app.get("/reports/daily")
    def read_daily_report(date: datetime.date) -> DailyReportResponse:
        return daily_report(database, date)

    return app


app = create_app()
