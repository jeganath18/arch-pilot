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

    architecture = (
        event.get("architecture")
        or event.get("executionArchitecture")
        or "UNKNOWN"
    )

    runtime_mode = event.get("runtimeMode", "FARGATE")
    hours = 730

    # ---------------------------------------------------------
    # Cost model
    # ---------------------------------------------------------

    if runtime_mode == "QEMU":
        # ArchPilot QEMU worker:
        # Graviton t4g.small = 2 vCPU / 2 GiB
        # Compared against same-size x86 t3.small.
        qemu_arm_hourly = 0.0112
        qemu_x86_hourly = 0.0224

        arm_monthly = qemu_arm_hourly * hours
        x86_monthly = qemu_x86_hourly * hours

        savings_usd = x86_monthly - arm_monthly
        savings_percent = (
            (savings_usd / x86_monthly) * 100
            if x86_monthly
            else 0
        )

        cost_comparison = {
            "model": "EC2_QEMU",
            "basis": (
                "EC2 compute only; 730 hours/month; "
                "Graviton t4g.small vs same-size x86 t3.small"
            ),
            "runtime": "Graviton EC2 + QEMU/binfmt",
            "hostArchitecture": "ARM64",
            "containerArchitecture": "AMD64",
            "instanceType": "t4g.small",
            "comparisonInstanceType": "t3.small",
            "vcpus": 2,
            "memoryGb": 2,
            "hoursPerMonth": hours,
            "arm64HourlyUsd": qemu_arm_hourly,
            "x86HourlyUsd": qemu_x86_hourly,
            "arm64MonthlyUsd": round(arm_monthly, 4),
            "x86MonthlyUsd": round(x86_monthly, 4),
            "estimatedSavingsUsd": round(savings_usd, 4),
            "estimatedSavingsPercent": round(savings_percent, 2),
            "pricingSource": "AWS EC2 On-Demand regional reference pricing",
            "excludes": [
                "ALB",
                "EBS",
                "data transfer",
                "public IPv4",
                "CloudWatch",
                "QEMU performance overhead"
            ]
        }

    else:
        # Fargate Linux pricing reference for Mumbai.
        # Use a valid 1 vCPU / 2 GB configuration.
        vcpus = 1
        memory_gb = 2

        fargate_x86_cpu = 0.04256
        fargate_x86_mem = 0.004655

        fargate_arm_cpu = 0.03405
        fargate_arm_mem = 0.00372

        x86_monthly = (
            vcpus * hours * fargate_x86_cpu
            + memory_gb * hours * fargate_x86_mem
        )

        arm_monthly = (
            vcpus * hours * fargate_arm_cpu
            + memory_gb * hours * fargate_arm_mem
        )

        savings_usd = x86_monthly - arm_monthly
        savings_percent = (
            (savings_usd / x86_monthly) * 100
            if x86_monthly
            else 0
        )

        cost_comparison = {
            "model": "FARGATE",
            "basis": (
                "Fargate compute only; Linux; "
                "1 vCPU + 2 GB; 730 hours/month"
            ),
            "runtime": "ECS Fargate",
            "hostArchitecture": "ARM64",
            "containerArchitecture": "ARM64",
            "vcpus": vcpus,
            "memoryGb": memory_gb,
            "hoursPerMonth": hours,
            "arm64HourlyCpuUsd": fargate_arm_cpu,
            "arm64HourlyMemoryUsd": fargate_arm_mem,
            "x86HourlyCpuUsd": fargate_x86_cpu,
            "x86HourlyMemoryUsd": fargate_x86_mem,
            "arm64MonthlyUsd": round(arm_monthly, 4),
            "x86MonthlyUsd": round(x86_monthly, 4),
            "estimatedSavingsUsd": round(savings_usd, 4),
            "estimatedSavingsPercent": round(savings_percent, 2),
            "pricingSource": "AWS Fargate regional reference pricing",
            "excludes": [
                "ALB",
                "data transfer",
                "CloudWatch",
                "additional ephemeral storage"
            ]
        }

    deployment = {
        "status": "HEALTHY",
        "architecture": architecture,
        "containerPort": event["containerPort"],
        "live_url": event.get("liveUrl") or os.environ["LIVE_URL"],
        "runtimeMode": runtime_mode,
    }

    # ECS deployments have a task definition.
    # QEMU deployments run directly on the Graviton worker.
    if event.get("taskDefinitionArn"):
        deployment["taskDefinitionArn"] = event["taskDefinitionArn"]

    report = {
        "jobId": job_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repository": event["repoUrl"],
        "decision": {
            "verdict": (
                event.get("decision")
                or {
                    "verdict": event["analysis"]["verdict"],
                    "confidence": event["analysis"]["confidence"],
                }
            )["verdict"],
            "confidence": (
                event.get("decision")
                or {
                    "verdict": event["analysis"]["verdict"],
                    "confidence": event["analysis"]["confidence"],
                }
            )["confidence"],
            "source": event.get("decisionSource", "deterministic"),
        },
        "scanner": event["analysis"],
        "build": {
            "status": event.get("buildStatus"),
            "buildId": event.get("buildId"),
            "imageUri": event["imageUri"],
            "platform": event["targetPlatform"],
        },
        "deployment": deployment,
        "costComparison": cost_comparison,
        "summary": (
            f"ArchPilot detected {event['analysis']['verdict']} for this workload, "
            f"built the container for {event['targetPlatform']}, and deployed it "
            f"using {runtime_mode} on {architecture}. "
            + (
                f"Estimated Graviton EC2 compute savings are "
                f"{savings_percent:.2f}% versus the equivalent x86 EC2 instance."
                if runtime_mode == "QEMU"
                else
                f"Estimated Fargate ARM64 compute savings are "
                f"{savings_percent:.2f}% versus Fargate x86."
            )
        ),
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
        live_url=event.get("liveUrl") or os.environ["LIVE_URL"],
        executionArchitecture=architecture,
        runtimeMode=runtime_mode,
    )

    return {**event, "reportKey": report_key}
