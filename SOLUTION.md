# Orders and Stock - Design and Trade-offs

## Scope and architecture

This implements **Option B**: accept orders, apply stock updates, and provide
a daily report.

```text
Client -> HTTP API -> PostgreSQL <- Stock worker
```

The API and worker run independently from one Python package. FastAPI and
Uvicorn provide the HTTP API; Psycopg handles database access.

An order is accepted after the order, its items, and its stock job commit.
Stock processing happens afterward. The API needs PostgreSQL to look up product
prices and save orders. It can continue accepting orders while the stock worker
is stopped.

## Data model

| Table | Key and saved data |
| --- | --- |
| `products` | `sku` primary key; name, current price in cents, and stock |
| `orders` | Caller-supplied `order_id` primary key; customer ID, total in cents, and acceptance time |
| `order_items` | `(order_id, sku)` primary key; quantity and unit price at creation; foreign keys to both parents |
| `stock_jobs` | `order_id` primary key and foreign key; nullable `processed_at` |

Order IDs and customer IDs are caller-supplied strings. Customer records remain
in the calling system. Callers reuse order IDs for retries; order IDs and SKUs
are assumed permanent.

Prices and totals use integer cents stored as `BIGINT`, assuming one currency
with 100 cents per unit. Each item's price is saved with the order, preserving
old totals when product prices change.

Request validation rejects blank identifiers, unknown fields, invalid
quantities, and repeated SKUs. New orders also require known SKUs.
SQL constraints enforce required values, foreign keys, positive quantities,
and non-negative prices and totals.

`stock_status` is derived from `processed_at`: null means `PENDING`;
a completion timestamp means `APPLIED`. This avoids a separate status column
but represents only these two states.

## Order creation and retries

For a new order, one connection and transaction:

1. Check for an existing order.
2. Read requested product prices and calculate the total.
3. Insert the order with `ON CONFLICT DO NOTHING`.
4. Save the priced items and one pending stock job.
5. Commit the transaction.

The API returns `201` after commit. A write failure rolls back the transaction.

The initial lookup handles ordinary retries. Two requests for the same order ID
can both check before either has saved it. The primary key and
`ON CONFLICT DO NOTHING` prevent duplicate orders.

Duplicates return `200` with the first order saved for that ID, even if a later
valid request has different contents. No additional items or jobs are saved.

## Stock processing and recovery

Each worker transaction locks one pending job with
`FOR UPDATE OF stock_jobs SKIP LOCKED`, reads its saved quantities, deducts
stock using database arithmetic, and sets `processed_at`. Deductions and
completion commit together.

| Interruption | Result |
| --- | --- |
| Before commit | Every deduction rolls back and the job remains pending. |
| After commit | Deductions and completion stay saved; the job is not selected again. |

Workers update products in the same SKU order to avoid getting stuck waiting on
each other's locks. Workers skip locked jobs, so jobs may finish in a different
order from acceptance.

Workers run independently against the same database and retry database failures
after a short delay. Pending jobs remain in PostgreSQL and are processed after
a restart.

## Daily report

The selected day runs from midnight UTC, inclusive, to the following midnight,
exclusive. The report returns order count, revenue in cents, units sold per SKU,
and current stock for every product, including unsold products. Empty totals
become zero.

Two queries share one `REPEATABLE READ` transaction:

1. Count orders and sum their saved totals for the day.
2. Group daily item quantities by SKU and join them to products and
   current stock.

Revenue is summed from `orders` so each order's total is counted once, even when
it has multiple item rows.

Both queries see the same committed data, even if other transactions save orders
or stock updates between them.

Accepted orders count as sales on their `accepted_at` day, including
`PENDING` orders. Worker delays do not change the sales day or totals.

Stock shows current levels, even for past report dates. It can lag sales while
the worker is stopped.

## Main trade-offs

| Choice | Benefit | Trade-off |
| --- | --- | --- |
| Separate API and worker with PostgreSQL jobs | Orders and jobs save together; workers restart independently. | Shared database and polling overhead. |
| `order_id` and `sku` as primary keys | Avoids extra generated IDs and extra unique indexes. | Larger text references; IDs must remain permanent. Generated keys help if IDs need changing. |
| Integer cents and saved prices | Exact calculations and stable historical totals. | Fractional cents need rounding rules; multiple currencies need explicit units. `NUMERIC`/`Decimal` offers decimal flexibility; floats approximate values and `money` depends on locale. |
| Connection per operation | Simple transactions and cleanup. | Connection setup on requests, jobs, and idle polls. A pool reuses connections but needs configuration and startup/shutdown handling. |
| Two queries in `REPEATABLE READ` | Consistent report results. | Extra database call and isolation setting. A single query would combine more logic. |
| UTC reporting days; count sales when accepted | Consistent dates independent of worker availability. | Includes pending orders and differs from South African calendar days. Counting completed jobs would delay or move sales. |

An index on `orders.accepted_at` supports report date filters; a partial index
on pending jobs supports worker searches. Both add storage and write overhead.

## Setup, tests, and demo

Python setup commands reuse the application's settings and execute packaged SQL
in one transaction. Seeding preserves existing products; reset deletes
application data by recreating the four tables and seeding them. Reset omits
`CASCADE`, so unexpected dependencies block it.

Unit tests cover validation and worker retry/stopping behavior.
Integration tests use temporary PostgreSQL schemas for concurrent duplicates,
multiple workers, rollback, restart recovery, saved prices, UTC boundaries,
and report consistency.

`make demo` demonstrates duplicate submissions and worker recovery through the
running API, with prompts to stop and restart the worker. Local setup and
commands are in [README.md](README.md).

## Limits

Stock reservations and insufficient-stock decisions are outside scope, so stock
can become negative. Authentication, payments, delivery, cancellations and
refunds are also excluded. There is no retry limit.
Schema migrations are not implemented.
