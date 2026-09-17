from decimal import Decimal
import os
import time
import boto3

codebuild = boto3.client("codebuild")
ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])

TERMINAL = {
    "SUCCEEDED": "SUCCEEDED",
    "FAILED": "FAILED",
    "FAULT": "FAILED",
    "STOPPED": "FAILED",
    "TIMED_OUT": "FAILED"
}

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
    build = codebuild.batch_get_builds(ids=[event["buildId"]])["builds"][0]
    status = build["buildStatus"]

    if status == "IN_PROGRESS":
        started = build.get("startTime")
        if started and time.time() - started.timestamp() > 15 * 60:
            update_job(event["jobId"], status="FAILED", stage="CODEBUILD", error="Build timeout exceeded.")
            return {**event, "buildStatus": "FAILED", "buildError": "Build timeout exceeded."}
        return {**event, "buildStatus": "IN_PROGRESS"}

    mapped = TERMINAL.get(status, "FAILED")
    update_job(
        event["jobId"],
        status="BUILT" if mapped == "SUCCEEDED" else "FAILED",
        stage="CODEBUILD",
        buildStatus=status,
        buildDurationMs=build.get("buildCompleteTime", build.get("startTime", 0))
            and int((build["buildCompleteTime"] - build["startTime"]).total_seconds() * 1000)
            if build.get("buildCompleteTime") and build.get("startTime") else 0,
        buildLogsUrl=build.get("logs", {}).get("deepLink"),
    )
    return {**event, "buildStatus": mapped}
