"""Storage layer. JsonStore for local dev, DynamoStore for AWS.

Pick with env STORE=json (default) or STORE=dynamo.
Same 4-table layout as the proposal: Products, Sales, Rates, Alerts.
Meta (simulated-day offset, fx-fail switch) lives in Rates under pair "__meta__".
"""
import json
import os
import threading
from decimal import Decimal

PAIR = "USDTHB"
META_PAIR = "__meta__"


class JsonStore:
    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "DATA_FILE", os.path.join(os.path.dirname(__file__), "data.json")
        )
        self._lock = threading.RLock()
        self._empty = {"products": {}, "sales": {}, "rates": [], "alerts": [], "meta": {}}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.d = json.load(f)
        else:
            self.d = json.loads(json.dumps(self._empty))

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # products
    def list_products(self):
        with self._lock:
            return sorted((dict(p) for p in self.d["products"].values()), key=lambda p: p["sku"])

    def get_product(self, sku):
        with self._lock:
            p = self.d["products"].get(sku)
            return dict(p) if p else None

    def put_product(self, p):
        with self._lock:
            self.d["products"][p["sku"]] = dict(p)
            self._save()

    def delete_product(self, sku):
        with self._lock:
            self.d["products"].pop(sku, None)
            self.d["sales"].pop(sku, None)
            self._save()

    # sales
    def add_sale(self, sku, date, qty):
        with self._lock:
            s = self.d["sales"].setdefault(sku, {})
            s[date] = s.get(date, 0) + qty
            self._save()

    def get_sales(self, sku, from_date):
        with self._lock:
            return {d: q for d, q in self.d["sales"].get(sku, {}).items() if d >= from_date}

    # rates
    def put_rate(self, rec):
        with self._lock:
            self.d["rates"].append(dict(rec))
            self.d["rates"] = self.d["rates"][-200:]
            self._save()

    def latest_rate(self):
        with self._lock:
            return dict(self.d["rates"][-1]) if self.d["rates"] else None

    def latest_ok_rate(self):
        with self._lock:
            for r in reversed(self.d["rates"]):
                if r.get("ok"):
                    return dict(r)
            return None

    # alerts
    def add_alert(self, rec):
        with self._lock:
            self.d["alerts"].append(dict(rec))
            self.d["alerts"] = self.d["alerts"][-500:]
            self._save()

    def list_alerts(self, limit=50):
        with self._lock:
            return [dict(a) for a in reversed(self.d["alerts"][-limit:])]

    # meta
    def get_meta(self):
        with self._lock:
            return dict(self.d["meta"])

    def set_meta(self, **kv):
        with self._lock:
            self.d["meta"].update(kv)
            self._save()

    def reset(self):
        with self._lock:
            self.d = json.loads(json.dumps(self._empty))
            self._save()


def _to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_ddb(obj):
    if isinstance(obj, list):
        return [_from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


class DynamoStore:
    """Cloud person: set env PRODUCTS_TABLE, SALES_TABLE, RATES_TABLE, ALERTS_TABLE."""

    def __init__(self):
        import boto3
        from boto3.dynamodb.conditions import Key

        self.Key = Key
        ddb = boto3.resource("dynamodb")
        self.products = ddb.Table(os.environ.get("PRODUCTS_TABLE", "Products"))
        self.sales = ddb.Table(os.environ.get("SALES_TABLE", "Sales"))
        self.rates = ddb.Table(os.environ.get("RATES_TABLE", "Rates"))
        self.alerts = ddb.Table(os.environ.get("ALERTS_TABLE", "Alerts"))

    @staticmethod
    def _scan(table):
        resp = table.scan()
        items = resp.get("Items", [])
        while "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items += resp.get("Items", [])
        return [_from_ddb(i) for i in items]

    def list_products(self):
        return sorted(self._scan(self.products), key=lambda p: p["sku"])

    def get_product(self, sku):
        item = self.products.get_item(Key={"sku": sku}).get("Item")
        return _from_ddb(item) if item else None

    def put_product(self, p):
        self.products.put_item(Item=_to_ddb(p))

    def delete_product(self, sku):
        self.products.delete_item(Key={"sku": sku})
        for item in self.sales.query(KeyConditionExpression=self.Key("sku").eq(sku))["Items"]:
            self.sales.delete_item(Key={"sku": sku, "date": item["date"]})

    def add_sale(self, sku, date, qty):
        self.sales.update_item(
            Key={"sku": sku, "date": date},
            UpdateExpression="ADD qty :q",
            ExpressionAttributeValues={":q": qty},
        )

    def get_sales(self, sku, from_date):
        resp = self.sales.query(
            KeyConditionExpression=self.Key("sku").eq(sku) & self.Key("date").gte(from_date)
        )
        return {i["date"]: int(i["qty"]) for i in resp["Items"]}

    def put_rate(self, rec):
        self.rates.put_item(Item=_to_ddb({"pair": PAIR, **rec}))

    def _latest(self, only_ok):
        resp = self.rates.query(
            KeyConditionExpression=self.Key("pair").eq(PAIR), ScanIndexForward=False, Limit=50
        )
        for r in resp["Items"]:
            if not only_ok or r.get("ok"):
                return _from_ddb(r)
        return None

    def latest_rate(self):
        return self._latest(False)

    def latest_ok_rate(self):
        return self._latest(True)

    def add_alert(self, rec):
        self.alerts.put_item(Item=_to_ddb(rec))

    def list_alerts(self, limit=50):
        items = sorted(self._scan(self.alerts), key=lambda a: a["timestamp"], reverse=True)
        return items[:limit]

    def get_meta(self):
        item = self.rates.get_item(Key={"pair": META_PAIR, "timestamp": "meta"}).get("Item")
        return _from_ddb(item) if item else {}

    def set_meta(self, **kv):
        meta = self.get_meta()
        meta.update(kv)
        meta.update({"pair": META_PAIR, "timestamp": "meta"})
        self.rates.put_item(Item=_to_ddb(meta))

    def reset(self):
        for table, keys in (
            (self.products, ("sku",)),
            (self.sales, ("sku", "date")),
            (self.rates, ("pair", "timestamp")),
            (self.alerts, ("date", "timestamp")),
        ):
            for item in self._scan(table):
                table.delete_item(Key={k: item[k] for k in keys})


_store = None


def get_store():
    global _store
    if _store is None:
        _store = DynamoStore() if os.environ.get("STORE", "json") == "dynamo" else JsonStore()
    return _store
