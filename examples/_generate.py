"""Generate the example datasets under examples/. Reproducible, stdlib-only.

    uv run python examples/_generate.py

Checked in alongside the generated files so the datasets are reproducible and
reviewable. Deterministic (seeded), no third-party deps — the CSVs it writes are
what `smolduck run examples/<name>` explores. See README → "Example datasets".

Two datasets, each chosen to exercise a different part of the workbench:

  examples/ecommerce/  — two related files (customers + orders). Folder-as-DB,
                         joins, revenue-over-time, channel mix, refunds.
  examples/churn/      — one wide labeled table. Profiling, correlation, and a
                         learnable classification target for the ML panel.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = 20260529

FIRST = [
    "Ada", "Alan", "Grace", "Katherine", "Edsger", "Barbara", "Donald", "Margaret",
    "Tim", "Radia", "Linus", "Hedy", "Dennis", "Vint", "Shafi", "Leslie",
    "John", "Sophie", "Marie", "Carl", "Ingrid", "Omar", "Yuki", "Priya",
    "Diego", "Lena", "Kofi", "Mei", "Noah", "Aisha",
]
LAST = [
    "Lovelace", "Turing", "Hopper", "Johnson", "Dijkstra", "Liskov", "Knuth",
    "Hamilton", "Berners-Lee", "Perlman", "Torvalds", "Lamarr", "Shannon",
    "Cerf", "Goldwasser", "Lamport", "Nakamoto", "Germain", "Curie", "Gauss",
]
COUNTRIES = ["US", "UK", "Germany", "France", "Brazil", "Japan", "Canada", "Australia"]
SEGMENTS = ["enterprise", "smb", "consumer"]
SEGMENT_WEIGHTS = [0.18, 0.34, 0.48]
CHANNELS = ["web", "mobile", "store"]
REGIONS = ["North", "South", "East", "West"]
PLANS = ["basic", "standard", "premium"]
CONTRACTS = ["month-to-month", "one-year", "two-year"]
PAYMENTS = ["card", "paypal", "bank-transfer", "invoice"]
REFUND_REASONS = ["damaged", "late_delivery", "wrong_item", "changed_mind", "not_as_described"]


def write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {path.relative_to(ROOT.parent)} ({len(rows)} rows)")


def date(rng: random.Random, year: int) -> str:
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return f"{year}-{month:02d}-{day:02d}"


# --------------------------------------------------------------- ecommerce

def gen_ecommerce(rng: random.Random) -> None:
    n_customers = 220
    customers = []
    seg_of: dict[int, str] = {}
    for cid in range(1, n_customers + 1):
        seg = rng.choices(SEGMENTS, SEGMENT_WEIGHTS)[0]
        seg_of[cid] = seg
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        customers.append((
            cid, name, rng.choice(COUNTRIES), seg,
            date(rng, rng.choice([2023, 2024])), rng.randint(21, 69),
        ))
    write_csv(ROOT / "ecommerce" / "customers.csv",
              ["customer_id", "name", "country", "segment", "signup_date", "age"],
              customers)

    # Enterprise buys bigger; mobile share rises through the year; small refund rate.
    base_amount = {"enterprise": 480.0, "smb": 160.0, "consumer": 55.0}
    orders = []
    oid = 10000
    for _ in range(3600):
        oid += 1
        cid = rng.randint(1, n_customers)
        seg = seg_of[cid]
        month = rng.randint(1, 12)
        order_date = f"2024-{month:02d}-{rng.randint(1, 28):02d}"
        # mobile weight grows over the months, store shrinks
        w_mobile = 0.25 + 0.04 * month
        w_store = max(0.05, 0.40 - 0.02 * month)
        channel = rng.choices(CHANNELS, [1.0, w_mobile, w_store])[0]
        items = max(1, round(rng.lognormvariate(0.7, 0.5)))
        noise = rng.lognormvariate(0.0, 0.45)
        amount = round(base_amount[seg] * (0.3 + 0.7 * items / 4) * noise, 2)
        roll = rng.random()
        status = ("refunded" if roll < 0.04 else
                  "cancelled" if roll < 0.09 else
                  "pending" if roll < 0.17 else "completed")
        orders.append((oid, cid, order_date, channel, status, items, amount))
    write_csv(ROOT / "ecommerce" / "orders.csv",
              ["order_id", "customer_id", "order_date", "channel", "status", "items", "amount"],
              orders)

    refunds = []
    rid = 0
    for o in orders:
        if o[4] == "refunded":
            rid += 1
            refunds.append((rid, o[0], round(o[6] * rng.uniform(0.5, 1.0), 2),
                            rng.choice(REFUND_REASONS), o[2]))
    write_csv(ROOT / "ecommerce" / "refunds.csv",
              ["refund_id", "order_id", "amount", "reason", "refund_date"], refunds)


# --------------------------------------------------------------- churn

def gen_churn(rng: random.Random) -> None:
    rows = []
    for sid in range(1, 1501):
        region = rng.choice(REGIONS)
        plan = rng.choices(PLANS, [0.45, 0.35, 0.20])[0]
        contract = rng.choices(CONTRACTS, [0.55, 0.28, 0.17])[0]
        payment = rng.choice(PAYMENTS)
        tenure = rng.randint(1, 72)
        base_charge = {"basic": 28, "standard": 58, "premium": 95}[plan]
        monthly = round(base_charge + rng.uniform(-6, 12), 2)
        total = round(monthly * tenure * rng.uniform(0.92, 1.05), 2)
        tickets = min(15, int(rng.expovariate(1 / 2.2)))
        has_dependents = rng.random() < 0.35

        # Learnable logit: month-to-month, short tenure, many tickets, high bill ↑ churn.
        z = (-1.1
             + (1.7 if contract == "month-to-month" else -0.6 if contract == "two-year" else 0.0)
             + 1.4 * (tenure < 8)
             - 0.018 * tenure
             + 0.22 * tickets
             + 0.012 * (monthly - 55)
             - 0.4 * has_dependents)
        p = 1 / (1 + math.exp(-z))
        churned = int(rng.random() < p)
        rows.append((sid, region, plan, contract, payment, tenure,
                     monthly, total, tickets, int(has_dependents), churned))
    write_csv(ROOT / "churn" / "subscriptions.csv",
              ["subscriber_id", "region", "plan", "contract", "payment_method",
               "tenure_months", "monthly_charges", "total_charges", "support_tickets",
               "has_dependents", "churned"],
              rows)
    rate = sum(r[-1] for r in rows) / len(rows)
    print(f"  churn rate ≈ {rate:.1%}")


def main() -> None:
    gen_ecommerce(random.Random(SEED))
    gen_churn(random.Random(SEED + 1))


if __name__ == "__main__":
    main()
