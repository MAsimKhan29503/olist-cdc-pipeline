"""Simulates live app activity on the source Postgres so CDC has changes to capture.

Only touches rows whose IDs start with 'sim-', so the real Olist data is never modified.
"""
import argparse
import os
import random
import time
import uuid
from datetime import datetime, timedelta

import psycopg2

DB = dict(
    host=os.getenv("SOURCE_DB_HOST", "localhost"),
    port=int(os.getenv("SOURCE_DB_PORT", "5435")),
    user=os.getenv("SOURCE_DB_USER", "postgres"),
    password=os.getenv("SOURCE_DB_PASSWORD", "postgres"),
    dbname=os.getenv("SOURCE_DB_NAME", "olist_source"),
)

CITIES = [
    ("sao paulo", "SP", "01310"),
    ("rio de janeiro", "RJ", "20040"),
    ("belo horizonte", "MG", "30110"),
    ("curitiba", "PR", "80010"),
    ("salvador", "BA", "40020"),
    ("porto alegre", "RS", "90010"),
]

# An order moves forward one step at a time
NEXT_STATUS = {"created": "approved", "approved": "shipped", "shipped": "delivered"}
# Which timestamp column to fill when an order reaches each status
TIMESTAMP_COL = {
    "approved": "order_approved_at",
    "shipped": "order_delivered_carrier_date",
    "delivered": "order_delivered_customer_date",
}


def new_order(cur, products):
    """A customer places a new order (sometimes a brand-new customer)."""
    cur.execute(
        "SELECT customer_id FROM customers WHERE starts_with(customer_id, 'sim-') "
        "ORDER BY random() LIMIT 1"
    )
    row = cur.fetchone()
    if row and random.random() < 0.3:  # 30% of orders come from a returning customer
        customer_id = row[0]
    else:
        customer_id = "sim-cust-" + uuid.uuid4().hex[:12]
        city, state, zip_prefix = random.choice(CITIES)
        cur.execute(
            "INSERT INTO customers (customer_id, customer_unique_id, customer_zip_code_prefix, "
            "customer_city, customer_state) VALUES (%s, %s, %s, %s, %s)",
            (customer_id, "sim-uniq-" + uuid.uuid4().hex[:12], zip_prefix, city, state),
        )

    order_id = "sim-" + uuid.uuid4().hex[:16]
    now = datetime.now()
    cur.execute(
        "INSERT INTO orders (order_id, customer_id, order_status, order_purchase_timestamp, "
        "order_estimated_delivery_date) VALUES (%s, %s, 'created', %s, %s)",
        (order_id, customer_id, now, now + timedelta(days=random.randint(7, 20))),
    )
    for item_id in range(1, random.randint(1, 3) + 1):
        product_id, seller_id, price = random.choice(products)
        cur.execute(
            "INSERT INTO order_items (order_id, order_item_id, product_id, seller_id, "
            "shipping_limit_date, price, freight_value) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (order_id, item_id, product_id, seller_id, now + timedelta(days=3),
             price, round(random.uniform(5, 40), 2)),
        )
    return f"NEW      order {order_id}"


def advance_status(cur):
    """An existing order moves to its next status (created -> approved -> shipped -> delivered)."""
    cur.execute(
        "SELECT order_id, order_status FROM orders WHERE starts_with(order_id, 'sim-') "
        "AND order_status IN ('created', 'approved', 'shipped') ORDER BY random() LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    order_id, status = row
    new_status = NEXT_STATUS[status]
    cur.execute(
        f"UPDATE orders SET order_status = %s, {TIMESTAMP_COL[new_status]} = %s WHERE order_id = %s",
        (new_status, datetime.now(), order_id),
    )
    return f"STATUS   order {order_id}: {status} -> {new_status}"


def update_address(cur):
    """A customer moves to a new city. This is what builds history for SCD Type 2 later."""
    cur.execute(
        "SELECT customer_id FROM customers WHERE starts_with(customer_id, 'sim-') "
        "ORDER BY random() LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    city, state, zip_prefix = random.choice(CITIES)
    cur.execute(
        "UPDATE customers SET customer_city = %s, customer_state = %s, "
        "customer_zip_code_prefix = %s WHERE customer_id = %s",
        (city, state, zip_prefix, row[0]),
    )
    return f"ADDRESS  customer {row[0]} -> {city}, {state}"


def cancel_order(cur):
    """A customer cancels an order that has not shipped yet."""
    cur.execute(
        "SELECT order_id FROM orders WHERE starts_with(order_id, 'sim-') "
        "AND order_status IN ('created', 'approved') ORDER BY random() LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    cur.execute("UPDATE orders SET order_status = 'canceled' WHERE order_id = %s", (row[0],))
    return f"CANCEL   order {row[0]}"


def delete_order(cur):
    """Old canceled orders are purged from the app database (a hard delete)."""
    cur.execute(
        "SELECT order_id FROM orders WHERE starts_with(order_id, 'sim-') "
        "AND order_status = 'canceled' ORDER BY random() LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    cur.execute("DELETE FROM order_items WHERE order_id = %s", (row[0],))
    cur.execute("DELETE FROM orders WHERE order_id = %s", (row[0],))
    return f"DELETE   order {row[0]}"


# (action, how often it is picked)
ACTIONS = [
    (new_order, 40),
    (advance_status, 35),
    (update_address, 10),
    (cancel_order, 8),
    (delete_order, 7),
]


def main():
    parser = argparse.ArgumentParser(description="Simulate app activity on the source DB")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between changes")
    parser.add_argument("--max-events", type=int, default=0, help="stop after N changes (0 = run forever)")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    # Real product/seller/price combinations, so fake order items look realistic
    cur.execute(
        "SELECT product_id, seller_id, price FROM order_items "
        "WHERE price IS NOT NULL ORDER BY random() LIMIT 500"
    )
    products = cur.fetchall()
    conn.commit()

    funcs = [f for f, _ in ACTIONS]
    weights = [w for _, w in ACTIONS]
    count = 0
    print("Simulator started. Press Ctrl+C to stop.")
    try:
        while args.max_events == 0 or count < args.max_events:
            action = random.choices(funcs, weights)[0]
            message = action(cur, products) if action is new_order else action(cur)
            if message is None:  # nothing suitable to change right now
                conn.rollback()
                continue
            conn.commit()  # Debezium only sees a change once it is committed
            count += 1
            print(f"{datetime.now():%H:%M:%S}  {message}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {count} changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()