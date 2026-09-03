"""Generate the synthetic grocery-loyalty warehouse used by the demo.

Deterministic (seeded) so the golden answers in data/golden.json stay valid.
Run: python -m data.seed  (or `make seed`).
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

from agent.config import settings

DATA_START = date(2024, 1, 1)
DATA_END = date(2026, 8, 31)
SEED = 20250903

PROVINCES = ["ON", "QC", "NS", "NB", "AB", "BC"]
PROVINCE_W = [0.34, 0.22, 0.12, 0.08, 0.12, 0.12]
SEGMENTS = ["Value Seeker", "Family Stock-Up", "Fresh Foodie", "Convenience", "Occasional"]
SEGMENT_W = [0.25, 0.2, 0.2, 0.2, 0.15]
TIERS = ["Bronze", "Silver", "Gold"]
TIER_W = [0.6, 0.3, 0.1]
BANNERS = ["Metro Fresh", "Neighbourhood Market", "Discount Depot"]
CATEGORIES = ["Produce", "Dairy", "Bakery", "Meat", "Pantry", "Frozen", "Beverages", "Household", "Health & Beauty"]
CATEGORY_W = [0.18, 0.14, 0.08, 0.14, 0.16, 0.08, 0.1, 0.07, 0.05]
BRANDS = ["Compliments", "Panache", "Kraft", "Nestle", "PepsiCo", "Danone", "Unilever", "P&G", "Local Farms", "Maple Leaf"]
CITIES = {
    "ON": ["Toronto", "Ottawa", "Mississauga", "Hamilton"],
    "QC": ["Montreal", "Quebec City", "Laval"],
    "NS": ["Halifax", "Dartmouth", "Stellarton"],
    "NB": ["Moncton", "Saint John"],
    "AB": ["Calgary", "Edmonton"],
    "BC": ["Vancouver", "Victoria"],
}

CAMPAIGNS = [
    # id, name, channel, start, end, discount, target segment, boosted category
    (1, "Winter Pantry Stock-Up", "flyer", date(2025, 1, 13), date(2025, 2, 9), 12.0, "Family Stock-Up", "Pantry"),
    (2, "Spring Fresh Produce", "app_push", date(2025, 4, 7), date(2025, 5, 4), 15.0, "Fresh Foodie", "Produce"),
    (3, "Summer Beverages Blitz", "email", date(2025, 6, 23), date(2025, 7, 20), 10.0, "Convenience", "Beverages"),
    (4, "Back-to-School", "flyer", date(2025, 8, 18), date(2025, 9, 7), 8.0, "Family Stock-Up", "Bakery"),
    (5, "Holiday Bakery Bonus Points", "in_store", date(2025, 12, 1), date(2025, 12, 24), 5.0, "Occasional", "Bakery"),
    (6, "Spring Fresh Produce 2026", "app_push", date(2026, 4, 6), date(2026, 5, 3), 15.0, "Fresh Foodie", "Produce"),
]


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def build(db_path: Path | None = None, n_customers: int = 1500, n_products: int = 300) -> Path:
    db_path = db_path or settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    rng = random.Random(SEED)

    # --- stores
    stores = []
    sid = 1
    for prov, w in zip(PROVINCES, PROVINCE_W):
        for _ in range(max(2, round(25 * w))):
            stores.append((sid, rng.choice(BANNERS), prov, rng.choice(CITIES[prov]), rng.randint(18000, 60000)))
            sid += 1

    # --- products
    products = []
    for pid in range(1, n_products + 1):
        cat = rng.choices(CATEGORIES, CATEGORY_W)[0]
        private = rng.random() < 0.32
        brand = "Compliments" if private else rng.choice(BRANDS[2:])
        price = round(rng.uniform(1.5, 24.0), 2)
        products.append((pid, f"{brand} {cat} Item {pid}", cat, brand, private, price))
    by_cat: dict[str, list[tuple]] = {}
    for p in products:
        by_cat.setdefault(p[2], []).append(p)

    # --- customers
    customers = []
    for cid in range(1, n_customers + 1):
        prov = rng.choices(PROVINCES, PROVINCE_W)[0]
        seg = rng.choices(SEGMENTS, SEGMENT_W)[0]
        tier = rng.choices(TIERS, TIER_W)[0]
        joined = date(2019, 1, 1) + timedelta(days=rng.randint(0, (date(2026, 6, 30) - date(2019, 1, 1)).days))
        customers.append((cid, f"LC-{rng.randint(10**7, 10**8 - 1)}", f"member{cid}@example.com", prov, seg, tier, joined))
    stores_by_prov: dict[str, list[tuple]] = {}
    for s in stores:
        stores_by_prov.setdefault(s[2], []).append(s)

    visit_rate = {"Value Seeker": 0.07, "Family Stock-Up": 0.05, "Fresh Foodie": 0.08, "Convenience": 0.16, "Occasional": 0.02}
    lines_mu = {"Value Seeker": 7, "Family Stock-Up": 13, "Fresh Foodie": 7, "Convenience": 4, "Occasional": 5}
    fresh = {"Produce", "Meat", "Bakery", "Dairy"}

    # A slice of customers stops shopping so lapse questions have signal.
    lapsed_after: dict[int, date] = {}
    for c in customers:
        r = rng.random()
        if r < 0.06:
            lapsed_after[c[0]] = DATA_END - timedelta(days=rng.randint(45, 89))   # at risk
        elif r < 0.12:
            lapsed_after[c[0]] = DATA_END - timedelta(days=rng.randint(90, 400))  # lapsed / churned

    transactions = []
    txn_id = 0
    for day in _daterange(DATA_START, DATA_END):
        active_campaigns = [c for c in CAMPAIGNS if c[3] <= day <= c[4]]
        season = 1.0 + 0.12 * (1 if day.month in (11, 12) else 0) - 0.05 * (1 if day.month in (1, 2) else 0)
        for c in customers:
            cid, _, _, prov, seg, tier, joined = c
            if day < joined or (cid in lapsed_after and day > lapsed_after[cid]):
                continue
            p_visit = visit_rate[seg] * season * (1.15 if day.weekday() in (4, 5) else 1.0)
            if rng.random() >= p_visit:
                continue
            txn_id += 1
            store = rng.choice(stores_by_prov[prov])
            n_lines = max(1, int(rng.gauss(lines_mu[seg], 3)))
            camp = next((cp for cp in active_campaigns if cp[6] == seg), None)
            for _ in range(n_lines):
                cat = rng.choices(CATEGORIES, CATEGORY_W)[0]
                if seg == "Fresh Foodie" and rng.random() < 0.5:
                    cat = rng.choice(sorted(fresh))
                campaign_id = None
                if camp and rng.random() < 0.55:
                    cat = camp[7]
                    campaign_id = camp[0]
                prod = rng.choice(by_cat[cat])
                if seg == "Value Seeker" and rng.random() < 0.45:
                    pl = [p for p in by_cat[cat] if p[4]]
                    if pl:
                        prod = rng.choice(pl)
                qty = 1 if rng.random() < 0.7 else rng.randint(2, 4)
                price = prod[5] * (1 - camp[5] / 100 if campaign_id else 1)
                amount = round(price * qty, 2)
                pts = int(amount) + (int(amount * 0.05) if tier == "Gold" else 0)
                transactions.append((txn_id, cid, store[0], prod[0], day, qty, amount, pts, campaign_id))

    import pandas as pd

    con = duckdb.connect(str(db_path))
    frames = {
        "customers": pd.DataFrame(customers, columns=["customer_id", "loyalty_card", "email", "province", "segment", "tier", "joined_on"]),
        "stores": pd.DataFrame(stores, columns=["store_id", "banner", "province", "city", "sqft"]),
        "products": pd.DataFrame(products, columns=["product_id", "name", "category", "brand", "is_private_label", "unit_price"]),
        "campaigns": pd.DataFrame([c[:7] for c in CAMPAIGNS], columns=["campaign_id", "name", "channel", "start_date", "end_date", "discount_pct", "target_segment"]),
        "transactions": pd.DataFrame(transactions, columns=["txn_id", "customer_id", "store_id", "product_id", "txn_date", "quantity", "line_amount", "points_earned", "campaign_id"]),
    }
    casts = {
        "customers": "customer_id::INTEGER customer_id, loyalty_card, email, province, segment, tier, joined_on::DATE joined_on",
        "stores": "store_id::INTEGER store_id, banner, province, city, sqft::INTEGER sqft",
        "products": "product_id::INTEGER product_id, name, category, brand, is_private_label::BOOLEAN is_private_label, unit_price::DECIMAL(10,2) unit_price",
        "campaigns": "campaign_id::INTEGER campaign_id, name, channel, start_date::DATE start_date, end_date::DATE end_date, discount_pct::DECIMAL(5,2) discount_pct, target_segment",
        "transactions": "txn_id::INTEGER txn_id, customer_id::INTEGER customer_id, store_id::INTEGER store_id, product_id::INTEGER product_id, txn_date::DATE txn_date, quantity::INTEGER quantity, line_amount::DECIMAL(10,2) line_amount, points_earned::INTEGER points_earned, campaign_id::INTEGER campaign_id",
    }
    for name, df in frames.items():
        con.register(f"_{name}", df)
        con.execute(f"CREATE TABLE {name} AS SELECT {casts[name]} FROM _{name}")
        con.unregister(f"_{name}")
    golden = _golden_answers(con)
    con.close()
    (db_path.parent / "golden.json").write_text(json.dumps(golden, indent=2, default=str))
    return db_path


def _golden_answers(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """Ground-truth values for the Langfuse dataset, computed from the seeded data."""
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    total, joined_2025 = q("SELECT COUNT(*), SUM(CASE WHEN joined_on BETWEEN '2025-01-01' AND '2025-12-31' THEN 1 ELSE 0 END) FROM customers")[0]
    q2 = q("SELECT s.banner, SUM(t.line_amount) FROM transactions t JOIN stores s USING(store_id) WHERE txn_date BETWEEN '2025-04-01' AND '2025-06-30' GROUP BY 1 ORDER BY 2 DESC")
    seg = q("SELECT c.segment, AVG(b.rev) FROM (SELECT txn_id, customer_id, SUM(line_amount) rev FROM transactions GROUP BY 1,2) b JOIN customers c USING(customer_id) GROUP BY 1 ORDER BY 2 DESC")
    pl = q("SELECT c.province, SUM(CASE WHEN p.is_private_label THEN t.line_amount ELSE 0 END)/SUM(t.line_amount) FROM transactions t JOIN customers c USING(customer_id) JOIN products p USING(product_id) WHERE txn_date BETWEEN '2025-01-01' AND '2025-12-31' AND c.province IN ('ON','QC') GROUP BY 1")
    lift = q("""
        WITH camp AS (SELECT SUM(t.line_amount)/28.0 d FROM transactions t JOIN products p USING(product_id) WHERE p.category='Produce' AND txn_date BETWEEN '2025-04-07' AND '2025-05-04'),
             base AS (SELECT SUM(t.line_amount)/28.0 d FROM transactions t JOIN products p USING(product_id) WHERE p.category='Produce' AND txn_date BETWEEN '2025-03-10' AND '2025-04-06')
        SELECT (camp.d-base.d)/base.d*100 FROM camp, base""")[0][0]
    active_june = q("SELECT COUNT(DISTINCT customer_id) FROM transactions WHERE txn_date BETWEEN '2025-06-01' AND '2025-06-30'")[0][0]
    top_pts = q("SELECT p.name, SUM(points_earned) FROM transactions t JOIN products p USING(product_id) WHERE txn_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
    total_pts = q("SELECT SUM(points_earned) FROM transactions WHERE txn_date BETWEEN '2025-01-01' AND '2025-12-31'")[0][0]
    bts_days = (date(2025, 9, 7) - date(2025, 8, 18)).days + 1
    bts_rev = q("SELECT SUM(line_amount) FROM transactions WHERE campaign_id=4")[0][0]
    at_risk = q("SELECT COUNT(*) FROM (SELECT customer_id, MAX(txn_date) last_txn FROM transactions GROUP BY 1) WHERE DATE '2026-08-31' - last_txn BETWEEN 45 AND 89")[0][0]
    sqft = q("SELECT s.store_id, s.banner, s.city, SUM(t.line_amount)/s.sqft FROM transactions t JOIN stores s USING(store_id) WHERE txn_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY 1,2,3,s.sqft ORDER BY 4 DESC LIMIT 1")[0]
    return {
        "members_total": {"value": total, "also": {"joined_2025": joined_2025}},
        "q2_2025_revenue": {"value": round(float(sum(r[1] for r in q2)), 2), "also": {"top_banner": q2[0][0]}},
        "top_segment_basket": {"value": round(float(seg[0][1]), 2), "also": {"segment": seg[0][0], "top3": [(s, round(float(v), 2)) for s, v in seg[:3]]}},
        "private_label_share": {"value": None, "also": {p: round(float(v) * 100, 1) for p, v in pl}},
        "spring_produce_lift_pct": {"value": round(float(lift), 1)},
        "active_members_june_2025": {"value": active_june},
        "top5_points_products": {"value": round(float(sum(r[1] for r in top_pts)) / float(total_pts) * 100, 1), "also": {"products": [r[0] for r in top_pts]}},
        "back_to_school": {"value": bts_days, "also": {"revenue": float(bts_rev), "revenue_per_day": round(float(bts_rev) / bts_days, 2)}},
        "at_risk_members": {"value": at_risk},
        "top_store_rev_per_sqft": {"value": round(float(sqft[3]), 2), "also": {"store_id": sqft[0], "banner": sqft[1], "city": sqft[2]}},
    }


if __name__ == "__main__":
    path = build()
    con = duckdb.connect(str(path), read_only=True)
    for t in ["customers", "stores", "products", "campaigns", "transactions"]:
        print(f"{t:14s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8,} rows")
    print(f"written {path}")
