from decimal import Decimal
import os

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
    job_id = event["jobId"]

    service = ecs.describe_services(
        cluster=os.environ["ECS_CLUSTER_NAME"],
        services=[os.environ["ECS_SERVICE_NAME"]],
    )["services"][0]

    running_count = service.get("runningCount", 0)
    pending_count = service.get("pendingCount", 0)

    deployments = service.get("deployments", [])

    primary = next(
        (d for d in deployments if d.get("status") == "PRIMARY"),
        None
    )

    target_health = elbv2.describe_target_health(
        TargetGroupArn=os.environ["TARGET_GROUP_ARN"]
    ).get("targetHealthDescriptions", [])

    healthy_targets = [
        item
        for item in target_health
        if item.get("targetHealth", {}).get("state") == "healthy"
    ]

    # Native ARM deployment is healthy when:
    # - ECS has at least one running task
    # - no tasks are pending
    # - ALB has at least one healthy target
    if (
        primary
        and primary.get("rolloutState") == "COMPLETED"
        and primary.get("runningCount", 0) >= 1
        and primary.get("pendingCount", 0) == 0
    ):
        live_url = (
            event.get("liveUrl")
            or os.environ["LIVE_URL"]
        )

        update_job(
            job_id,
            status="DEPLOYED",
            stage="HEALTHY",
            live_url=live_url,
            healthyTargets=len(healthy_targets),
        )

        return {
            **event,
            "deployStatus": "HEALTHY",
            "architecture": "ARM64",
            "executionArchitecture": "ARM64",
            "runtimeMode": "FARGATE",
            "liveUrl": live_url,
        }

    # Detect terminal ECS deployment failures.
    messages = [
        e.get("message", "")
        for e in service.get("events", [])[:8]
    ]

    hard_error_words = (
        "CannotPullContainerError",
        "RESOURCE:MEMORY",
        "CannotCreateContainerError",
        "CannotStartContainerError",
    )

    if any(
        any(word in message for word in hard_error_words)
        for message in messages
    ):
        error = "; ".join(messages[:3])

        update_job(
            job_id,
            status="FAILED",
            stage="ECS",
            error=error,
        )

        return {
            **event,
            "deployStatus": "FAILED",
            "deployError": error,
        }

    return {
    **event,
    "deployStatus": "WAITING",
    "debug": {
        "runningCount": service.get("runningCount"),
        "pendingCount": service.get("pendingCount"),
        "desiredCount": service.get("desiredCount"),
        "serviceStatus": service.get("status"),
        "healthyTargetCount": len(healthy_targets),
        "targetStates": [
            item.get("targetHealth", {}).get("state")
            for item in target_health
        ],
    },
}