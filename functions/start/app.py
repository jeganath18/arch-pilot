from decimal import Decimal
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
sfn = boto3.client("stepfunctions")

def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body),
    }

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        repo_url = str(body.get("repoUrl", "")).strip()
        if not repo_url.startswith("https://github.com/"):
            return response(400, {"message": "repoUrl must be a public GitHub HTTPS URL."})

        job_id = f"ap-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc).isoformat()

        ddb.put_item(Item={
            "jobId": job_id,
            "repoUrl": repo_url,
            "status": "QUEUED",
            "stage": "QUEUED",
            "createdAt": created_at,
        })

        execution = sfn.start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=job_id,
            input=json.dumps({
                "jobId": job_id,
                "repoUrl": repo_url,
                "createdAt": created_at,
            }),
        )

        ddb.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET executionArn = :arn",
            ExpressionAttributeValues={":arn": execution["executionArn"]},
        )

        return response(202, {
            "jobId": job_id,
            "status": "QUEUED",
        })
    except Exception as exc:
        return response(500, {"message": "Failed to start ArchPilot job.", "error": str(exc)})
