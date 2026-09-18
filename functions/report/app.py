from decimal import Decimal
import json
import os
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
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

def monthly_cost(cpu_hours, mem_gb_hours, cpu_rate, mem_rate, tasks=1):
    return tasks * ((cpu_hours * cpu_rate) + (mem_gb_hours * mem_rate))

def lambda_handler(event, context):
    job_id = event["jobId"]
    # Demo default: 1 vCPU + 1 GB, 730 hours/month. Rates are configurable via env vars.
    hours = 730
    vcpus = 1
    memory_gb = 1

    x86_cpu = float(os.environ["FARGATE_X86_VCPU_HOURLY"])
    x86_mem = float(os.environ["FARGATE_X86_MEM_GB_HOURLY"])
    arm_cpu = float(os.environ["FARGATE_ARM_VCPU_HOURLY"])
    arm_mem = float(os.environ["FARGATE_ARM_MEM_GB_HOURLY"])

    x86 = (vcpus * hours * x86_cpu) + (memory_gb * hours * x86_mem)
    arm = (vcpus * hours * arm_cpu) + (memory_gb * hours * arm_mem)

    savings = ((x86 - arm) / x86 * 100) if x86 else 0

    report = {
        "jobId": job_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repository": event["repoUrl"],
        "decision": {
            "verdict": (event.get("decision") or {"verdict": event["analysis"]["verdict"]})["verdict"],
            "confidence": (event.get("decision") or {"confidence": event["analysis"]["confidence"]})["confidence"],
            "source": event.get("decisionSource", "deterministic")
        },
        "scanner": event["analysis"],
        "build": {
            "status": event.get("buildStatus"),
            "buildId": event.get("buildId"),
            "imageUri": event["imageUri"],
            "platform": event["targetPlatform"]
        },
        "deployment": {
            "status": "HEALTHY",
            "architecture": event["architecture"],
            "taskDefinitionArn": event["taskDefinitionArn"],
            "containerPort": event["containerPort"],
            "live_url" : event.get("liveUrl") or os.environ["LIVE_URL"]
        },
        "costComparison": {
            "basis": "Fargate compute only; 1 vCPU + 1 GB + 730 hours/month",
            "x86MonthlyUsd": round(x86, 4),
            "arm64MonthlyUsd": round(arm, 4),
            "estimatedSavingsUsd": round(x86 - arm, 4),
            "estimatedSavingsPercent": round(savings, 2),
            "pricingSource": os.environ["PRICING_SOURCE"]
        },
        "summary": (
            f"ArchPilot recommends {event['architecture']} for this workload, "
            f"built the container for {event['targetPlatform']}, and deployed it "
            f"to ECS Fargate. Estimated Fargate compute difference is "
            f"{savings:.2f}% under the configured pricing assumptions."
        )
    }

    report_key = f"jobs/{job_id}/report.json"
    s3.put_object(
        Bucket=os.environ["REPORTS_BUCKET"],
        Key=report_key,
        Body=json.dumps(report, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
    )

    update_job(
        job_id,
        status="COMPLETED",
        stage="COMPLETE",
        reportKey=report_key,
        live_url = event.get("liveUrl") or os.environ["LIVE_URL"]
    )

    return {**event, "reportKey": report_key}
