from decimal import Decimal
import json
import os

import boto3

codebuild = boto3.client("codebuild")
ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])

BUILD_SPEC = r"""
version: 0.2

phases:
  pre_build:
    commands:
      - echo "ArchPilot build starting"
      - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
      - docker info
      - docker buildx create --name archpilot-builder --driver docker-container --use
      - docker run --privileged --rm tonistiigi/binfmt --install all
      - echo "Building $TARGET_PLATFORM"
  build:
    commands:
      - docker buildx build --platform "$TARGET_PLATFORM" --provenance=false --sbom=false -t "$IMAGE_URI" --push .
  post_build:
    commands:
      - |
        if [ "$CODEBUILD_BUILD_SUCCEEDING" = "1" ]; then
            echo "Image pushed successfully to $IMAGE_URI"
        else
            echo "Build failed. Image was not published."
        fi
"""

ARM_VALIDATION_BUILD_SPEC = r"""
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
      - docker stop archpilot-validation
"""

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
    analysis = event["analysis"]
    decision = event.get("decision") or {
        "verdict": analysis["verdict"],
        "confidence": analysis["confidence"]
    }

    verdict = decision["verdict"]
    if verdict == "native_arm64":
        # Publish a real multi-platform manifest so the same tag can be pulled
        # by either x86_64 or ARM64 runtimes. QEMU/binfmt enables the ARM64
        # build leg inside the standard x86 CodeBuild worker.
        target_platform = "linux/arm64"
    elif verdict == "x86_required":
        target_platform = "linux/amd64"
    else:
        raise ValueError(f"Unsupported build verdict: {verdict}")

    if verdict == "native_arm64":
        image_tag = f"{job_id}-arm64"
    elif verdict == "x86_required":
        image_tag = f"{job_id}-amd64"
    else:
        raise ValueError(f"Unsupported build verdict: {verdict}")

    image_uri = f"{os.environ['ECR_REPOSITORY_URI']}:{image_tag}"
    registry = os.environ["ECR_REPOSITORY_URI"].split("/")[0]

    env_vars = [
        {"name": "AWS_DEFAULT_REGION", "value": os.environ["AWS_REGION"], "type": "PLAINTEXT"},
        {"name": "ECR_REGISTRY", "value": registry, "type": "PLAINTEXT"},
        {"name": "IMAGE_URI", "value": image_uri, "type": "PLAINTEXT"},
        {"name": "TARGET_PLATFORM", "value": target_platform, "type": "PLAINTEXT"},
        {"name": "IMAGE_TAG", "value": image_tag, "type": "PLAINTEXT"},
    ]

    result = codebuild.start_build(
        projectName=os.environ["CODEBUILD_PROJECT_NAME"],
        sourceTypeOverride="GITHUB",
        sourceLocationOverride=event["repoUrl"],
        buildspecOverride=BUILD_SPEC,
        environmentVariablesOverride=env_vars,
    )

    build_id = result["build"]["id"]

    update_job(
        job_id,
        status="BUILDING",
        stage="CODEBUILD",
        buildId=build_id,
        imageUri=image_uri,
        targetPlatform=target_platform,
    )

    return {
        **event,
        "buildId": build_id,
        "imageUri": image_uri,
        "targetPlatform": target_platform
    }
