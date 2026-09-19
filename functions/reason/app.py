from decimal import Decimal
import json
import os

import boto3

ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
bedrock = boto3.client("bedrock-runtime")

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["native_arm64", "x86_required"]
        },
        "confidence": {
            "type": "number"
        },
        "explanation": {
            "type": "string"
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "recommendedRuntime": {
            "type": "string",
            "enum": [
                "FARGATE_ARM64",
                "QEMU"
            ]
        }
    },
    "required": [
        "verdict",
        "confidence",
        "explanation",
        "risks",
        "recommendedRuntime"
    ],
    "additionalProperties": False
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
    job_id = event["jobId"]
    update_job(job_id, status="AI_ANALYSIS", stage="BEDROCK")

    analysis = event["analysis"]

    evidence = []
    for finding in analysis.get("findings", []):
        if finding["severity"] == "ambiguous":
            # Intentionally pass normalized evidence, not arbitrary repository text.
            evidence.append({
                "type": finding["type"],
                "evidence": finding.get("evidence"),
                "message": finding.get("message"),
            })

    prompt = {
        "targetArchitecture": "ARM64 / AWS Graviton",
        "instruction": (
            "Determine whether the supplied repository evidence supports native "
            "Linux ARM64 execution on AWS Graviton. "
            "Use only the supplied scanner evidence. Never invent package support "
            "or architecture compatibility. "
            "If the evidence clearly supports ARM64, return native_arm64. "
            "If there is credible evidence of x86_64-only behavior, return "
            "x86_required. "
            "If evidence is incomplete or ambiguous, prefer x86_required rather "
            "than claiming native ARM64 compatibility. "
            "For x86_required, recommend QEMU as the first runtime strategy, "
            "because ArchPilot preserves the AMD64 image and attempts to execute "
            "it on an ARM64 Graviton host using QEMU/binfmt. "
            "Do not recommend rebuilding an AMD64 image into ARM64."
        ),
        "scannerEvidence": evidence,
        "dockerSummary": {
            "baseImages": analysis["docker"].get("baseImages", []),
            "containerPort": analysis["docker"].get("containerPort", 8080)
        },
        "dependencySummary": analysis.get("dependencies", {}),
        "nativeBinaries": analysis.get("nativeBinaries", [])
    }

    model_id = os.environ["BEDROCK_MODEL_ID"]

    response = bedrock.converse(
        modelId=model_id,
        system=[{
            "text": (
                "You are an infrastructure compatibility reviewer. "
                "Decide whether an application is safe to build and run natively "
                "on Linux ARM64. Use only supplied evidence."
            )
        }],
        messages=[{
            "role": "user",
            "content": [{"text": json.dumps(prompt, separators=(",", ":"))}]
        }],
        inferenceConfig={
            "maxTokens": 700,
            "temperature": 0
        },
        outputConfig={
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": json.dumps(SCHEMA, separators=(",", ":")),
                        "name": "archpilot_decision",
                        "description": "Architecture recommendation for ArchPilot"
                    }
                }
            }
        }
    )

    text = next(
        item["text"]
        for item in response["output"]["message"]["content"]
        if "text" in item
    )
    decision = json.loads(text)

    update_job(
        job_id,
        status="DECIDED",
        stage="DECISION",
        verdict=decision["verdict"],
        confidence=decision["confidence"],
        decisionSource="bedrock",
        bedrockExplanation=decision["explanation"],
        bedrockRisks=decision["risks"],
        recommendedRuntime=decision["recommendedRuntime"],
    )

    return {
        **event,
        "decision": decision,
        "decisionSource": "bedrock"
    }
