from decimal import Decimal
import os
import time

import boto3

ecs = boto3.client("ecs")
elbv2 = boto3.client("elbv2")
ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])

def ddb_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value

def update_job(job_id, **values):
    expr = []
    names = {}
    vals = {}
    for key, value in values.items():
        attr = f"#{key}"
        val = f":{key}"
        names[attr] = key
        vals[val] = ddb_value(value)
        expr.append(f"{attr} = {val}")
    ddb.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(expr),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )

def lambda_handler(event, context):
    service = ecs.describe_services(
        cluster=os.environ["ECS_CLUSTER_NAME"],
        services=[os.environ["ECS_SERVICE_NAME"]]
    )["services"][0]

    deployments = service.get("deployments", [])
    primary = next((d for d in deployments if d.get("status") == "PRIMARY"), None)

    target_health = elbv2.describe_target_health(
        TargetGroupArn=os.environ["TARGET_GROUP_ARN"]
    ).get("targetHealthDescriptions", [])

    healthy_targets = [
        item for item in target_health
        if item.get("targetHealth", {}).get("state") == "healthy"
    ]

    if service.get("runningCount", 0) >= 1 and service.get("pendingCount", 0) == 0 and healthy_targets:
        update_job(
            event["jobId"],
            status="DEPLOYED",
            stage="HEALTHY",
            liveUrl=os.environ["LIVE_URL"],
            healthyTargets=len(healthy_targets)
        )
        return {**event, "deployStatus": "HEALTHY"}

    # If an ECS service event shows a terminal deployment problem, surface it.
    messages = [e.get("message", "") for e in service.get("events", [])[:8]]
    hard_error_words = ("CannotPullContainerError", "RESOURCE:MEMORY", "CannotCreateContainerError")
    if any(any(word in msg for word in hard_error_words) for msg in messages):
        update_job(event["jobId"], status="FAILED", stage="ECS", error="; ".join(messages[:3]))
        return {**event, "deployStatus": "FAILED", "deployError": "; ".join(messages[:3])}

    # Step Functions handles the wait loop.
    return {**event, "deployStatus": "WAITING"}
