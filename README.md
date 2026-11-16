# CS446 Smart Inventory & Auto Restock Alert

Web app: stock check, restock analysis, USD/THB alerts.

## Run locally (no AWS needed)
```
python backend/local_server.py        # http://localhost:8000
python -m unittest discover -s backend/tests
```
Data in `backend/data.json` (JSON file replaces DynamoDB). Email = console print replaces SNS.
Env: `PORT`, `CHECK_EVERY` (sec, 300), `FX_EVERY` (sec, 3600), 0 = off.

## Layout
- `frontend/index.html` single-file dashboard. Set `API_URL` at top of script for cloud.
- `backend/core.py` pure logic. `service.py` operations. `store.py` JsonStore + DynamoStore.
- `backend/lambda_api.py`, `lambda_checker.py`, `lambda_fx.py` Lambda entry points (`lambda_handler`).
- API routes listed in docstring of `lambda_api.py`.

## Cloud handoff
Zip `backend/` (exclude `tests`, `data.json`, `local_server.py`) -> 3 Lambdas, handlers
`lambda_api.lambda_handler`, `lambda_checker.lambda_handler`, `lambda_fx.lambda_handler`.
Env vars for all three: `STORE=dynamo`, `SNS_TOPIC_ARN`, optional `PRODUCTS_TABLE` `SALES_TABLE`
`RATES_TABLE` `ALERTS_TABLE` (defaults Products/Sales/Rates/Alerts, keys per proposal section 4).
Function URL needs CORS (allow origin of S3 page, methods GET/POST/PUT/DELETE, header Content-Type).
Only stdlib + boto3 (already in Lambda runtime). `DynamoStore` untested against real AWS.
