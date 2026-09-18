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


ARM_VALIDATION_SPEC = r"""
version: 0.2

phases:
  pre_build:
    commands:
      - echo "========================================"
      - echo "ArchPilot ARM64 Compatibility Test"
      - echo "========================================"
      - docker info
      - docker buildx create --name archpilot-validator --driver docker-container --use
      - docker run --privileged --rm tonistiigi/binfmt --install arm64
      - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

  build:
    commands:
      - echo "Building application for linux/arm64"
      - docker buildx build --platform linux/arm64 --provenance=false --sbom=false -t "$VALIDATION_IMAGE" --load .

  post_build:
    commands:
      - echo "Starting ARM64 compatibility test"
      - docker run -d --name archpilot-validation -p 8080:8080 --platform linux/arm64 "$VALIDATION_IMAGE"
      - sleep 5
      - docker ps
      - curl --fail --max-time 10 http://127.0.0.1:8080
      - echo "ARM64 compatibility validation PASSED"
      - docker logs archpilot-validation
      - docker commit archpilot-validation "$ARM_IMAGE_URI"
      - docker push "$ARM_IMAGE_URI"
      - docker stop archpilot-validation
"""


def lambda_handler(event, context):
    job_id = event["jobId"]

    update_job(
        job_id,
        status="VALIDATING",
        stage="ARM_VALIDATION",
        validationStatus="RUNNING",
    )

    arm_image_uri = (
        f"{os.environ['ECR_REPOSITORY_URI']}:{job_id}-arm64"
    )

    validation_image = f"archpilot-validation:{job_id}"

    env_vars = [
        {
            "name": "AWS_DEFAULT_REGION",
            "value": os.environ["AWS_REGION"],
            "type": "PLAINTEXT",
        },
        {
            "name": "ECR_REGISTRY",
            "value": os.environ["ECR_REPOSITORY_URI"].split("/")[0],
            "type": "PLAINTEXT",
        },
        {
            "name": "VALIDATION_IMAGE",
            "value": validation_image,
            "type": "PLAINTEXT",
        },
        {
            "name": "ARM_IMAGE_URI",
            "value": arm_image_uri,
            "type": "PLAINTEXT",
        },
    ]

    result = codebuild.start_build(
        projectName=os.environ["CODEBUILD_PROJECT_NAME"],
        sourceTypeOverride="GITHUB",
        sourceLocationOverride=event["repoUrl"],
        buildspecOverride=ARM_VALIDATION_SPEC,
        environmentVariablesOverride=env_vars,
    )

    validation_build_id = result["build"]["id"]

    update_job(
        job_id,
        validationBuildId=validation_build_id,
    )

    return {
        **event,
        "validationBuildId": validation_build_id,
        "armImageUri": arm_image_uri,
    }
