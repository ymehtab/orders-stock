# Orders and Stock

A Python API that saves orders in PostgreSQL, updates stock through a separate
worker, and provides a daily report.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Docker Compose.

```bash
make install
make postgres-start
make db-init
make db-seed
```

The default database URL is `postgresql://orders:orders@127.0.0.1:54329/orders`.
To use another PostgreSQL database, export `DATABASE_URL` and skip
`make postgres-start`.

## Usage

### Start the API and worker

Start the API and worker in separate terminals:

```bash
make run-api       # Terminal 1
make run-worker    # Terminal 2
```

Run `make run-worker` in additional terminals to start more workers against the
same database.

Try the API through [Swagger UI](http://127.0.0.1:8000/docs) in your browser, or
run the `curl` examples below.

### Create an order

```bash
curl -i http://127.0.0.1:8000/orders \
  -H 'Content-Type: application/json' \
  -d '{
    "order_id": "web-100045",
    "customer_id": "cust-42",
    "items": [
      {"sku": "BAN-001", "quantity": 2},
      {"sku": "APL-003", "quantity": 1}
    ]
  }'
```

Orders require `order_id`, `customer_id`, and a nonempty `items` list.
Each item needs a known `sku` and a positive integer `quantity`. Each SKU must
appear only once per order; repeated SKUs return `422` (validation error).
Both IDs are caller-supplied strings, such as `web-100045` and `cust-42`.
Reuse the same `order_id` when retrying an order. Responses identify orders by
`order_id`.

### Daily report

With the API running, request today's UTC report in another terminal:

```bash
curl "http://127.0.0.1:8000/reports/daily?date=$(date -u +%F)"
```

For another day, replace `$(date -u +%F)` with a date in `YYYY-MM-DD` format.

Example response for `2026-11-19`, after the sample order's stock update has
completed:

```json
{
  "date": "2026-11-19",
  "timezone": "UTC",
  "total_orders": 1,
  "revenue_cents": 697,
  "products": [
    {
      "sku": "APL-003",
      "units_sold": 1,
      "current_stock": 29
    },
    {
      "sku": "BAN-001",
      "units_sold": 2,
      "current_stock": 48
    }
  ]
}
```

Sales cover the selected UTC day and include pending orders. Stock reflects
current levels, even when requesting a past date.

## Demo

With the API and one worker running, use a third terminal:

```bash
make demo
```

The demo checks duplicates, then prompts you to stop and restart the worker to
verify stock catch-up. Each run saves two orders and prints concise pass
summaries. Failed checks show expected and actual values.

## Behavior

- New orders return `201`; duplicate order IDs return `200` and the original
  order. Prices come from the database and are saved with the order.
- Orders are accepted while the worker is stopped. `stock_status` changes
  from `PENDING` to `APPLIED` when stock deductions commit.

## Development

```bash
make test               # Run all tests; PostgreSQL required
make test-unit          # Run unit tests; no database needed
make test-integration   # Run PostgreSQL integration tests
make coverage           # Report statement and branch coverage; PostgreSQL required
make check              # Check formatting and linting
make fmt                # Apply formatting
make lint               # Check for lint errors
make clean              # Remove the virtual environment and generated files
```

Integration tests use temporary schemas. Set `TEST_DATABASE_URL` to use a
different database.

## Database maintenance

Stop the API and worker with Ctrl-C before resetting the database or stopping
PostgreSQL.

To drop and recreate the four application tables and restore sample products:

```bash
make db-reset
```

This deletes all application data in the configured database and restores the
sample prices and stock.

To stop PostgreSQL while preserving its data:

```bash
make postgres-stop
```

## AI tool usage

I provided the initial project structure and design, and directed the
implementation and simplification. I used AI to help implement and review code,
generate and refine tests and draft documentation.

I reviewed the changes against the requirements, with validation through pytest
and Ruff.
