CREATE TABLE IF NOT EXISTS customers (
    id BIGINT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    region VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id BIGINT PRIMARY KEY,
    region VARCHAR(64) NOT NULL,
    amount NUMERIC(18, 2) NOT NULL,
    sold_at DATE NOT NULL
);

INSERT INTO customers (id, name, region) VALUES
    (1, '星河科技', '华东'),
    (2, '南方零售', '华南')
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales (id, region, amount, sold_at) VALUES
    (1, '华东', 1200.00, DATE '2026-08-01'),
    (2, '华南', 980.00, DATE '2026-08-02'),
    (3, '华东', 760.00, DATE '2026-08-03')
ON CONFLICT (id) DO NOTHING;
