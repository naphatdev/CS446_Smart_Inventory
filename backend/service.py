"""Operations used by the api, checker and fx Lambdas (and the local server)."""
import json
import random
import urllib.request
from datetime import date, datetime, timedelta, timezone

import core
from notify import send_email
from store import PAIR, get_store

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=THB"
DEFAULT_RATE = 35.0


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------- time ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def today(store=None):
    """Virtual today = real UTC date + simulated day offset."""
    store = store or get_store()
    offset = store.get_meta().get("day_offset", 0)
    return date.today() + timedelta(days=offset)


def window_dates(store=None):
    t = today(store)
    return [(t - timedelta(days=i)).isoformat() for i in range(core.WINDOW_DAYS - 1, -1, -1)]


# ---------- fx ----------
def current_rate(store=None):
    """Return dict: rate, stale, timestamp, source. Falls back to last good rate."""
    store = store or get_store()
    good = store.latest_ok_rate()
    last = store.latest_rate()
    if not good:
        return {"rate": DEFAULT_RATE, "stale": True, "timestamp": None, "source": "default"}
    return {
        "rate": good["rate"],
        "stale": not last.get("ok", False),
        "timestamp": good["timestamp"],
        "source": good.get("source", "frankfurter"),
    }


def fetch_frankfurter():
    req = urllib.request.Request(FRANKFURTER_URL, headers={"User-Agent": "smart-inventory"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.load(resp)
    return float(data["rates"]["THB"])


def refresh_rate(store=None, fetch=fetch_frankfurter):
    """Never raises. On failure records ok=False and keeps the last good rate."""
    store = store or get_store()
    ts = now_iso()
    try:
        if store.get_meta().get("fx_fail"):
            raise RuntimeError("API down (simulated)")
        rate = fetch()
        store.put_rate({"timestamp": ts, "rate": rate, "source": "frankfurter", "ok": True})
    except Exception as e:  # noqa: BLE001 - any failure must fall back
        good = store.latest_ok_rate()
        store.put_rate(
            {
                "timestamp": ts,
                "rate": good["rate"] if good else None,
                "source": "frankfurter",
                "ok": False,
                "error": str(e)[:200],
            }
        )
    return current_rate(store)


# ---------- products ----------
def _validate(body, partial=False):
    out = {}
    if "name" in body or not partial:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ApiError("name required")
        out["name"] = name
    for key, cast in (("stock", int), ("min_stock", int), ("cost_usd", float)):
        if key in body or not partial:
            try:
                v = cast(body.get(key, 0))
            except (TypeError, ValueError):
                raise ApiError(f"{key} must be a number")
            if v < 0:
                raise ApiError(f"{key} must be >= 0")
            out[key] = v
    return out


def _analyzed(store, p, rate, dates):
    sales = store.get_sales(p["sku"], dates[0])
    sold = sum(q for d, q in sales.items() if d in dates)
    return core.analyze(p, sold, rate)


def list_products(store=None):
    store = store or get_store()
    rate = current_rate(store)["rate"]
    dates = window_dates(store)
    return [_analyzed(store, p, rate, dates) for p in store.list_products()]


def create_product(body, store=None):
    store = store or get_store()
    sku = str(body.get("sku", "")).strip().upper()
    if not sku:
        raise ApiError("sku required")
    if store.get_product(sku):
        raise ApiError("sku already exists", 409)
    p = {"sku": sku, **_validate(body)}
    p.update({"last_status": core.STATUS_IN, "ref_rate": current_rate(store)["rate"], "fx_alerted": False})
    store.put_product(p)
    return list_one(store, sku)


def list_one(store, sku):
    p = store.get_product(sku)
    if not p:
        raise ApiError("product not found", 404)
    return _analyzed(store, p, current_rate(store)["rate"], window_dates(store))


def update_product(sku, body, store=None):
    store = store or get_store()
    p = store.get_product(sku)
    if not p:
        raise ApiError("product not found", 404)
    p.update(_validate(body, partial=True))
    store.put_product(p)
    return list_one(store, sku)


def delete_product(sku, store=None):
    store = store or get_store()
    if not store.get_product(sku):
        raise ApiError("product not found", 404)
    store.delete_product(sku)
    return {"deleted": sku}


def sell(sku, qty=1, store=None):
    store = store or get_store()
    p = store.get_product(sku)
    if not p:
        raise ApiError("product not found", 404)
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise ApiError("qty must be a number")
    if qty < 1:
        raise ApiError("qty must be >= 1")
    if qty > p["stock"]:
        raise ApiError("not enough stock")
    p["stock"] -= qty
    store.put_product(p)
    store.add_sale(sku, today(store).isoformat(), qty)
    return list_one(store, sku)


def restock(sku, qty, store=None):
    """Record a purchase: add stock and lock in today's rate as ref_rate."""
    store = store or get_store()
    p = store.get_product(sku)
    if not p:
        raise ApiError("product not found", 404)
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise ApiError("qty must be a number")
    if qty < 1:
        raise ApiError("qty must be >= 1")
    p["stock"] += qty
    p["ref_rate"] = current_rate(store)["rate"]
    p["fx_alerted"] = False
    store.put_product(p)
    return list_one(store, sku)


def simulate_day(store=None, rng=random):
    """Advance virtual clock one day and create plausible sales."""
    store = store or get_store()
    store.set_meta(day_offset=store.get_meta().get("day_offset", 0) + 1)
    d = today(store).isoformat()
    rate = current_rate(store)["rate"]
    dates = window_dates(store)
    sold = []
    for p in store.list_products():
        avg = _analyzed(store, p, rate, dates)["avg_daily"]
        qty = max(1, round(avg * rng.uniform(0.8, 1.4))) if avg > 0 else rng.randint(0, 2)
        qty = min(qty, p["stock"])
        if qty > 0:
            p["stock"] -= qty
            store.put_product(p)
            store.add_sale(p["sku"], d, qty)
        sold.append({"sku": p["sku"], "qty": qty})
    return {"date": d, "sold": sold}


# ---------- checker ----------
def _alert(store, sku, type_, message):
    ts = now_iso()
    rec = {"date": ts[:10], "timestamp": ts, "sku": sku, "type": type_, "message": message}
    store.add_alert(rec)
    try:
        send_email(f"[Smart Inventory] {type_} - {sku}", message)
    except Exception as e:  # noqa: BLE001 - email failure must not stop the check
        print(f"[notify] failed: {e}")
    return rec


def run_check(store=None):
    store = store or get_store()
    info = current_rate(store)
    rate = info["rate"]
    dates = window_dates(store)
    fired = []
    for p in store.list_products():
        a = _analyzed(store, p, rate, dates)
        changed = False

        if core.should_alert_status(p.get("last_status"), a["status"]):
            if a["status"] == core.STATUS_OUT:
                type_ = "OUT_OF_STOCK"
                msg = f"{a['name']} ({p['sku']}) is OUT OF STOCK."
            else:
                type_ = "LOW_STOCK"
                left = "unknown" if a["days_left"] is None else f"{a['days_left']} days"
                msg = (
                    f"{a['name']} ({p['sku']}) is LOW: stock {a['stock']}, min {a['min_stock']}, "
                    f"runs out in {left}."
                )
            msg += f" Recommended order: {a['reorder_qty']} units (~{a['reorder_cost_thb']:,.2f} THB)."
            fired.append(_alert(store, p["sku"], type_, msg))
        if p.get("last_status") != a["status"]:
            p["last_status"] = a["status"]
            changed = True

        if core.fx_exceeds(rate, p.get("ref_rate")):
            if not p.get("fx_alerted"):
                diff = p["cost_usd"] * (rate - p["ref_rate"])
                msg = (
                    f"Import cost up {diff:,.2f} THB per unit for {a['name']} ({p['sku']}): "
                    f"rate {rate:.4f} vs {p['ref_rate']:.4f} at last order."
                )
                fired.append(_alert(store, p["sku"], "FX_COST", msg))
                p["fx_alerted"] = True
                changed = True
        elif p.get("fx_alerted"):
            p["fx_alerted"] = False
            changed = True

        if changed:
            store.put_product(p)
    return {"checked": len(store.list_products()), "alerts": fired}


# ---------- dashboard ----------
def dashboard(store=None):
    store = store or get_store()
    info = current_rate(store)
    dates = window_dates(store)
    products = [_analyzed(store, p, info["rate"], dates) for p in store.list_products()]
    counts = {core.STATUS_IN: 0, core.STATUS_LOW: 0, core.STATUS_OUT: 0}
    for p in products:
        counts[p["status"]] += 1
    series = []
    for p in products:
        sales = store.get_sales(p["sku"], dates[0])
        series.append({"sku": p["sku"], "name": p["name"], "data": [sales.get(d, 0) for d in dates]})
    return {
        "today": today(store).isoformat(),
        "summary": {
            "total": len(products),
            "in_stock": counts[core.STATUS_IN],
            "low": counts[core.STATUS_LOW],
            "out": counts[core.STATUS_OUT],
            "stock_value_thb": round(sum(p["stock_value_thb"] for p in products), 2),
        },
        "rate": {**info, "pair": PAIR, "api_down_switch": bool(store.get_meta().get("fx_fail"))},
        "products": products,
        "restock": [p for p in products if p["reorder_qty"] > 0],
        "chart": {"dates": dates, "series": series},
        "alerts": store.list_alerts(30),
    }


# ---------- demo ----------
def seed(store=None):
    store = store or get_store()
    rate = current_rate(store)["rate"]
    t = today(store)
    demo = [
        ("KB-001", "Mechanical Keyboard", 15, 10, 25.0, [2, 3, 3, 2, 3, 4, 3]),
        ("MS-002", "Wireless Mouse", 40, 15, 12.0, [3, 4, 2, 3, 4, 3, 3]),
        ("HB-003", "USB-C Hub", 30, 10, 18.0, [1, 2, 1, 2, 1, 2, 1]),
    ]
    for sku, name, stock, min_stock, cost, hist in demo:
        store.put_product(
            {
                "sku": sku, "name": name, "stock": stock, "min_stock": min_stock,
                "cost_usd": cost, "ref_rate": rate, "fx_alerted": False,
                "last_status": core.compute_status(stock, min_stock, sum(hist) / core.WINDOW_DAYS),
            }
        )
        for i, q in enumerate(hist):
            store.add_sale(sku, (t - timedelta(days=core.WINDOW_DAYS - 1 - i)).isoformat(), q)
    return {"seeded": len(demo)}


def reset_demo(store=None):
    store = store or get_store()
    store.reset()
    refresh_rate(store)
    seed(store)
    return {"reset": True}


def ensure_seeded(store=None):
    store = store or get_store()
    if not store.list_products() and store.latest_rate() is None:
        reset_demo(store)
