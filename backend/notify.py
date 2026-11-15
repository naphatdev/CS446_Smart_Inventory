"""Email notifier. SNS when SNS_TOPIC_ARN is set, else print to console (local dev)."""
import os


def send_email(subject, message):
    arn = os.environ.get("SNS_TOPIC_ARN")
    if not arn:
        print(f"[notify:local] {subject} | {message}")
        return "local"
    import boto3

    boto3.client("sns").publish(TopicArn=arn, Subject=subject[:100], Message=message)
    return "sns"
