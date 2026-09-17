from decimal import Decimal
import json
import os

import boto3
from botocore.exceptions import ClientError

ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
s3 = boto3.client("s3")

def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, default=str),
    }

def lambda_handler(event, context):
    job_id = event["pathParameters"]["jobId"]
    item = ddb.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not item:
        return response(404, {"message": "Job not found."})

    report = None
    if item.get("reportKey"):
        try:
            obj = s3.get_object(Bucket=os.environ["REPORTS_BUCKET"], Key=item["reportKey"])
            report = json.loads(obj["Body"].read())
        except ClientError:
            report = None

    body = dict(item)
    if report is not None:
        body["report"] = report

    return response(200, body)
