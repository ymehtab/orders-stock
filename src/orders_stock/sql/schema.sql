CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents BIGINT NOT NULL CHECK (price_cents >= 0),
    stock INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    total_cents BIGINT NOT NULL CHECK (total_cents >= 0),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT REFERENCES orders(order_id),
    sku TEXT REFERENCES products(sku),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents BIGINT NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE IF NOT EXISTS stock_jobs (
    order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS orders_accepted_at_idx ON orders (accepted_at);
CREATE INDEX IF NOT EXISTS stock_jobs_pending_idx ON stock_jobs (order_id) WHERE processed_at IS NULL;
