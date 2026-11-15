"""Pure business logic. No I/O. Shared by api, checker and fx Lambdas."""
import math

STATUS_IN = "In Stock"
STATUS_LOW = "Low Stock"
STATUS_OUT = "Out of Stock"
STATUS_RANK = {STATUS_IN: 0, STATUS_LOW: 1, STATUS_OUT: 2}

WINDOW_DAYS = 7
LOW_DAYS = 3
TARGET_DAYS = 10
FX_ALERT_PCT = 0.02


def avg_daily(total_last_7_days):
    return total_last_7_days / WINDOW_DAYS


def days_left(stock, avg):
    """None means unknown (no sales history)."""
    if avg <= 0:
        return None
    return stock / avg


def compute_status(stock, min_stock, avg):
    if stock <= 0:
        return STATUS_OUT
    left = days_left(stock, avg)
    if stock < min_stock or (left is not None and left <= LOW_DAYS):
        return STATUS_LOW
    return STATUS_IN


def target_stock(min_stock, avg):
    return max(min_stock, math.ceil(avg * TARGET_DAYS))


def reorder_qty(stock, min_stock, avg):
    return max(0, target_stock(min_stock, avg) - stock)


def analyze(product, sold_last_7, rate):
    """Return product dict plus computed fields."""
    stock = product["stock"]
    min_stock = product["min_stock"]
    avg = avg_daily(sold_last_7)
    left = days_left(stock, avg)
    qty = reorder_qty(stock, min_stock, avg)
    cost_usd = product["cost_usd"]
    return {
        **product,
        "sold_7d": sold_last_7,
        "avg_daily": round(avg, 3),
        "days_left": None if left is None else round(left, 1),
        "status": compute_status(stock, min_stock, avg),
        "target_stock": target_stock(min_stock, avg),
        "reorder_qty": qty,
        "cost_thb": round(cost_usd * rate, 2),
        "reorder_cost_thb": round(qty * cost_usd * rate, 2),
        "stock_value_thb": round(stock * cost_usd * rate, 2),
    }


def should_alert_status(old_status, new_status):
    """Alert only when status gets worse."""
    return STATUS_RANK[new_status] > STATUS_RANK.get(old_status, 0)


def fx_exceeds(rate, ref_rate):
    return bool(ref_rate) and rate > ref_rate * (1 + FX_ALERT_PCT)
