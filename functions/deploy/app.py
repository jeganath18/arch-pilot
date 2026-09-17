from decimal import Decimal
import os
import boto3

ecs = boto3.client("ecs")
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

def lambda_handler(event, context):
    job_id = event["jobId"]
    target_platform = event["targetPlatform"]
    cpu_arch = "ARM64" if target_platform == "linux/arm64" else "X86_64"
    port = int(event.get("containerPort") or event["analysis"]["docker"].get("containerPort") or 8080)

    task_def = ecs.register_task_definition(
        family=f"archpilot-{job_id}",
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="512",
        memory="1024",
        executionRoleArn=os.environ["TASK_EXECUTION_ROLE_ARN"],
        runtimePlatform={
            "cpuArchitecture": cpu_arch,
            "operatingSystemFamily": "LINUX"
        },
        containerDefinitions=[{
            "name": "app",
            "image": event["imageUri"],
            "essential": True,
            "portMappings": [{
                "containerPort": port,
                "hostPort": port,
                "protocol": "tcp"
            }],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": os.environ["APP_LOG_GROUP"],
                    "awslogs-region": os.environ["AWS_REGION"],
                    "awslogs-stream-prefix": job_id,
                }
            }
        }],
        tags=[
            {"key": "Project", "value": "ArchPilot"},
            {"key": "JobId", "value": job_id}
        ]
    )

    task_definition_arn = task_def["taskDefinition"]["taskDefinitionArn"]

    ecs.update_service(
        cluster=os.environ["ECS_CLUSTER_NAME"],
        service=os.environ["ECS_SERVICE_NAME"],
        taskDefinition=task_definition_arn,
        desiredCount=1,
        forceNewDeployment=True,
        loadBalancers=[{
            "targetGroupArn": os.environ["TARGET_GROUP_ARN"],
            "containerName": "app",
            "containerPort": port
        }]
    )

    update_job(
        job_id,
        status="DEPLOYING",
        stage="ECS",
        architecture=cpu_arch,
        taskDefinitionArn=task_definition_arn,
        containerPort=port,
    )

    return {
        **event,
        "taskDefinitionArn": task_definition_arn,
        "architecture": cpu_arch,
        "containerPort": port
    }
