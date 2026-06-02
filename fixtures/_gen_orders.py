"""Generate fixtures/orders.parquet. Run once via `uv run python fixtures/_gen_orders.py`.

Kept in the repo so the Parquet fixture is reproducible. Orders reference
customers (1-12) and the refunded orders match refunds.json.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REFUNDED = {1003, 1009, 1015, 1021, 1027, 1030}
CHANNELS = ["web", "mobile", "store"]
STATUSES = ["completed", "pending", "cancelled"]

rows = []
for i in range(30):
    order_id = 1001 + i
    customer_id = (i % 12) + 1
    month = 1 + (i % 8)
    day = 1 + (i * 3) % 27
    order_date = f"2024-{month:02d}-{day + 1:02d}"
    amount = round(15.0 + (i * 37) % 480 + (i % 5) * 4.25, 2)
    status = "refunded" if order_id in REFUNDED else STATUSES[i % 3]
    channel = CHANNELS[i % 3]
    rows.append((order_id, customer_id, order_date, amount, status, channel))

table = pa.table(
    {
        "order_id": pa.array([r[0] for r in rows], pa.int64()),
        "customer_id": pa.array([r[1] for r in rows], pa.int64()),
        "order_date": pa.array([r[2] for r in rows], pa.string()),
        "amount": pa.array([r[3] for r in rows], pa.float64()),
        "status": pa.array([r[4] for r in rows], pa.string()),
        "channel": pa.array([r[5] for r in rows], pa.string()),
    }
)

out = Path(__file__).resolve().parent / "orders.parquet"
pq.write_table(table, out)
print(f"wrote {out} ({table.num_rows} rows)")
