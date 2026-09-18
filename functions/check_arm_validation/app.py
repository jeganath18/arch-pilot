import os
from decimal import Decimal

import boto3


ssm = boto3.client("ssm")
ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def ddb_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def update_job(job_id, **values):
    expressions = []
    names = {}
    vals = {}

    for key, value in values.items():
        attr_name = f"#{key}"
        attr_value = f":{key}"

        names[attr_name] = key
        vals[attr_value] = ddb_value(value)
        expressions.append(f"{attr_name} = {attr_value}")

    ddb.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(expressions),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def lambda_handler(event, context):
    job_id = event["jobId"]
    command_id = event["validationCommandId"]
    instance_id = os.environ["QEMU_WORKER_INSTANCE_ID"]

    result = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=instance_id,
    )

    status = result["Status"]

    if status == "Success":
        validation_status = "SUCCEEDED"

        update_job(
            job_id,
            validationStatus=validation_status,
            qemuStatus="HEALTHY",
            runtimeMode="QEMU",
            executionArchitecture="ARM64",
        )

    elif status in {
        "Failed",
        "Cancelled",
        "TimedOut",
        "Cancelling",
    }:
        validation_status = "FAILED"

        error = (
            result.get("StandardErrorContent")
            or result.get("StatusDetails")
            or "QEMU runtime validation failed"
        )

        update_job(
            job_id,
            validationStatus=validation_status,
            qemuStatus="FAILED",
            qemuError=error[-2000:],
        )

    else:
        validation_status = "RUNNING"

        update_job(
            job_id,
            validationStatus=validation_status,
        )

    return {
        **event,
        "validationStatus": validation_status,
        "qemuStatus": (
            "HEALTHY"
            if validation_status == "SUCCEEDED"
            else "FAILED"
            if validation_status == "FAILED"
            else "RUNNING"
        ),
    }