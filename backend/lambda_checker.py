"""Lambda "checker". Handler: lambda_checker.lambda_handler. Trigger: EventBridge every 5 min."""
import service


def lambda_handler(event, context):
    result = service.run_check()
    return {"checked": result["checked"], "alerts": len(result["alerts"])}
