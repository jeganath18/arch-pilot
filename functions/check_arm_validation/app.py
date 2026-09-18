import os
import boto3
from decimal import Decimal

codebuild = boto3.client("codebuild")
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
    validation_build_id = event["validationBuildId"]

    result = codebuild.batch_get_builds(
        ids=[validation_build_id]
    )

    builds = result.get("builds", [])

    if not builds:
        raise ValueError(
            f"ARM validation build not found: {validation_build_id}"
        )

    build = builds[0]
    status = build["buildStatus"]

    if status == "SUCCEEDED":
        validation_status = "SUCCEEDED"
    elif status in {
        "FAILED",
        "FAULT",
        "STOPPED",
        "TIMED_OUT",
    }:
        validation_status = "FAILED"
    else:
        validation_status = "RUNNING"

    update_job(
        job_id,
        validationStatus=validation_status,
    )

    return {
        **event,
        "validationStatus": validation_status,
    }