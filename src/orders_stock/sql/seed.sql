INSERT INTO products (sku, name, price_cents, stock)
VALUES
    ('BAN-001', 'Bananas 1kg', 199, 50),
    ('APL-003', 'Apples', 299, 30)
ON CONFLICT (sku) DO NOTHING;
