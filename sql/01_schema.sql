CREATE TABLE customers (
    customer_id              text PRIMARY KEY,
    customer_unique_id       text,
    customer_zip_code_prefix text,
    customer_city            text,
    customer_state           text
);

CREATE TABLE orders (
    order_id                      text PRIMARY KEY,
    customer_id                   text,
    order_status                  text,
    order_purchase_timestamp      timestamp,
    order_approved_at             timestamp,
    order_delivered_carrier_date  timestamp,
    order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp
);

CREATE TABLE order_items (
    order_id            text NOT NULL,
    order_item_id       integer NOT NULL,
    product_id          text,
    seller_id           text,
    shipping_limit_date timestamp,
    price               numeric(10,2),
    freight_value       numeric(10,2),
    PRIMARY KEY (order_id, order_item_id)
);

-- Make Postgres log the FULL old row on updates and deletes
ALTER TABLE customers   REPLICA IDENTITY FULL;
ALTER TABLE orders      REPLICA IDENTITY FULL;
ALTER TABLE order_items REPLICA IDENTITY FULL;