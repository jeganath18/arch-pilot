# ArchPilot Backend

AI-assisted multi-architecture deployment pipeline for public GitHub repositories.

## Pipeline

```text
POST /jobs
   |
   v
API Gateway
   |
   v
Start Lambda
   |
   v
Step Functions
   |
   +--> Analyze Lambda
   |      |
   |      +--> GitHub archive
   |      +--> deterministic scanner
   |      +--> S3 scanner report
   |
   +--> [ambiguous] Bedrock reasoning
   |
   +--> Start CodeBuild
   |      |
   |      +--> Docker Buildx
   |      +--> QEMU/binfmt for cross-builds
   |      +--> ECR push
   |
   +--> Deploy Lambda
   |      |
   |      +--> register ECS task definition
   |      +--> update shared ECS service
   |
   +--> wait for ECS/ALB health
   |
   +--> Report Lambda
          |
          +--> S3 decision report
          +--> DynamoDB job state

GET /jobs/{jobId}
```

## Important runtime decision

The MVP **uses QEMU in CodeBuild for cross-architecture builds/validation**. It does not assume that an arbitrary x86 binary can be transparently emulated on ECS Fargate/Graviton at runtime. The safe deployment path is:

- `native_arm64` -> CodeBuild publishes a `linux/amd64,linux/arm64` manifest; ECS deploys the ARM64/Graviton variant.
- `x86_required` -> CodeBuild publishes `linux/amd64`; ECS deploys X86_64 Fargate.

A future experimental runtime-emulation mode can be added with a custom Graviton EC2 fleet where QEMU/binfmt is installed on the host.

## AWS resources

- API Gateway
- Lambda
- Step Functions
- Amazon Bedrock
- CodeBuild
- ECR
- ECS Fargate
- Application Load Balancer
- S3
- DynamoDB
- CloudWatch Logs

## Requirements

- AWS CLI
- AWS SAM CLI
- Docker
- An AWS account with Bedrock model access enabled
- A public GitHub repository containing a Dockerfile

## Deploy

```bash
sam build
sam deploy --guided
```

Recommended parameter values:

- `ProjectName`: `archpilot`
- `BedrockModelId`: `us.amazon.nova-2-lite-v1:0` (or an inference profile/model ID available in your account/region)
- `FargateX86VcpuPerHour`, `FargateX86MemoryPerGBHour`,
  `FargateArmVcpuPerHour`, `FargateArmMemoryPerGBHour`: set to the
  current rates for your AWS region.

The cost report is **Fargate compute-only** and intentionally excludes the
shared ALB, network transfer, ECR, and CloudWatch costs. AWS pricing varies by
region and can change, so these parameters are configurable.

## API

### Start job

```http
POST /jobs
Content-Type: application/json

{
  "repoUrl": "https://github.com/example/project"
}
```

Response:

```json
{
  "jobId": "ap-...",
  "status": "QUEUED"
}
```

### Get status

```http
GET /jobs/{jobId}
```

## Local scanner test

```bash
python3 scanner/scanner.py examples/arm-compatible
python3 scanner/scanner.py examples/x86-required
python3 scanner/scanner.py examples/ambiguous
```

## First AWS milestone

Before building the UI, make this work:

```text
Dockerfile
  -> CodeBuild
  -> ECR
  -> ECS Fargate ARM64
  -> ALB
  -> live URL
```
