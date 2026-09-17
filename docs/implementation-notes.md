# Implementation Notes

## Backend language

The MVP uses Python 3.12 for every Lambda. This keeps the AWS backend dependency-free because boto3 is provided by the Lambda runtime.

## Scanner location

The analyzer Lambda contains a copy of the scanner and compatibility JSON so it can run without a container image or Lambda layer. The root `scanner/` directory is the same implementation used for local tests.

## Long-running jobs

The API returns immediately with a job ID. Step Functions owns the workflow state. DynamoDB exposes user-visible status while S3 stores detailed JSON reports.

## Build source

CodeBuild uses a trusted inline buildspec from the StartBuild Lambda and overrides the project source with the public GitHub repository URL. This avoids requiring every user repository to contain an ArchPilot buildspec.

## Deployment model

The MVP has one shared ECS Fargate service and one ALB. Each successful job replaces the previous live demo application. This is deliberate: a hackathon demo needs one reliable live endpoint, not a full multi-tenant PaaS.

## QEMU

QEMU/binfmt is enabled inside the privileged CodeBuild environment so an x86 build worker can build/test ARM64 images. It is **not** assumed to be available as a transparent runtime emulator inside Fargate. If runtime emulation is required later, move that mode to an ECS/EC2 Graviton host fleet where the operator can install binfmt/QEMU on the host.
