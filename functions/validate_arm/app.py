import os
from decimal import Decimal

import boto3


ssm = boto3.client("ssm")
ec2 = boto3.client("ec2")
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


def get_worker_public_ip():
    instance_id = os.environ["QEMU_WORKER_INSTANCE_ID"]

    response = ec2.describe_instances(
        InstanceIds=[instance_id]
    )

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            public_ip = instance.get("PublicIpAddress")

            if public_ip:
                return public_ip

    raise ValueError("QEMU worker public IP not found")


def lambda_handler(event, context):
    job_id = event["jobId"]
    image_uri = event["imageUri"]

    region = os.environ.get("AWS_REGION", "ap-south-1")
    worker_public_ip = get_worker_public_ip()

    live_url = f"http://{worker_public_ip}:8080"

    registry = image_uri.split("/")[0]

    commands = [
        "set -eux",

        # Remove any previous demo container.
        "docker rm -f archpilot-app 2>/dev/null || true",

        # Login to ECR.
        (
            f"aws ecr get-login-password --region {region} "
            f"| docker login --username AWS --password-stdin {registry}"
        ),

        # Pull the ORIGINAL x86 image.
        f"docker pull {image_uri}",

        # Run the SAME amd64 image on the ARM64 Graviton host.
        (
            f"docker run -d "
            f"--name archpilot-app "
            f"--restart unless-stopped "
            f"--platform linux/amd64 "
            f"-p 8080:8080 "
            f"{image_uri}"
        ),

        # Give the container a moment to start.
        "sleep 5",

        # Confirm the container is actually running.
        "docker ps --filter name=archpilot-app",

        # Confirm the application responds.
        "curl --fail --max-time 10 http://127.0.0.1:8080",

        # Show container logs for debugging/demo evidence.
        "docker logs archpilot-app",
    ]

    result = ssm.send_command(
        InstanceIds=[os.environ["QEMU_WORKER_INSTANCE_ID"]],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands,
            "executionTimeout": ["60"],
        },
        CloudWatchOutputConfig={
            "CloudWatchOutputEnabled": False
        },
    )

    command_id = result["Command"]["CommandId"]

    update_job(
        job_id,
        status="VALIDATING",
        stage="QEMU_VALIDATION",
        validationStatus="RUNNING",
        validationCommandId=command_id,
        executionArchitecture="ARM64",
        runtimeMode="QEMU",
        live_url = event.get("liveUrl") or os.environ["LIVE_URL"],
    )

    return {
        **event,
        "validationCommandId": command_id,
        "validationStatus": "RUNNING",
        "executionArchitecture": "ARM64",
        "runtimeMode": "QEMU",
        "live_url" : event.get("liveUrl") or os.environ["LIVE_URL"],
    }