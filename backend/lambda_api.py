"""Lambda "api". Handler: lambda_api.lambda_handler (Function URL event format).

Routes (JSON in, JSON out):
  GET    /dashboard
  GET    /products
  POST   /products                      {sku,name,stock,min_stock,cost_usd}
  PUT    /products/{sku}                {name?,stock?,min_stock?,cost_usd?}
  DELETE /products/{sku}
  POST   /products/{sku}/sell           {qty?=1}
  POST   /products/{sku}/restock        {qty}
  POST   /simulate                      advance 1 virtual day
  POST   /check                         run stock check now
  POST   /fx/refresh                    fetch USD/THB now
  POST   /fx/fail                       {down: true|false}  demo: fake API outage
  POST   /reset                         reset demo data
CORS: set on the Function URL config in AWS (the local server adds it itself).
"""
import json

import service
from service import ApiError


def route(method, path, body):
    parts = [p for p in path.split("/") if p]
    s = service
    if method == "GET" and parts == ["dashboard"]:
        return s.dashboard()
    if parts[:1] == ["products"]:
        if len(parts) == 1:
            if method == "GET":
                return s.list_products()
            if method == "POST":
                return s.create_product(body)
        elif len(parts) == 2:
            if method == "PUT":
                return s.update_product(parts[1], body)
            if method == "DELETE":
                return s.delete_product(parts[1])
        elif len(parts) == 3 and method == "POST":
            if parts[2] == "sell":
                return s.sell(parts[1], body.get("qty", 1))
            if parts[2] == "restock":
                return s.restock(parts[1], body.get("qty"))
    if method == "POST":
        if parts == ["simulate"]:
            return s.simulate_day()
        if parts == ["check"]:
            return s.run_check()
        if parts == ["fx", "refresh"]:
            return s.refresh_rate()
        if parts == ["fx", "fail"]:
            s.get_store().set_meta(fx_fail=bool(body.get("down", True)))
            return s.dashboard()["rate"]
        if parts == ["reset"]:
            return s.reset_demo()
    raise ApiError("not found", 404)


def handle(method, path, raw_body):
    """Framework-free entry used by both Lambda and the local server. Returns (status, obj)."""
    try:
        body = json.loads(raw_body) if raw_body else {}
        if not isinstance(body, dict):
            raise ApiError("body must be a JSON object")
        return 200, route(method.upper(), path, body)
    except ApiError as e:
        return e.status, {"error": str(e)}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}
    except Exception as e:  # noqa: BLE001
        print(f"[api] error: {e!r}")
        return 500, {"error": "internal error"}


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", event.get("httpMethod", "GET"))
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}
    path = event.get("rawPath", event.get("path", "/"))
    status, obj = handle(method, path, event.get("body"))
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(obj, ensure_ascii=False),
    }
