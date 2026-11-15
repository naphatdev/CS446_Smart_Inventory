"""Lambda "fx". Handler: lambda_fx.lambda_handler. Trigger: EventBridge every 1 hour."""
import service


def lambda_handler(event, context):
    return service.refresh_rate()
