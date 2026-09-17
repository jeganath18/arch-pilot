# ArchPilot Backend Architecture

## Why Step Functions

The pipeline contains long-running work: repository analysis, CodeBuild,
container build/push, ECS rollout and ALB health stabilization. Step Functions
keeps the workflow state outside the Lambda request lifecycle.

## Why deterministic analysis first

The scanner handles high-confidence signals:

- explicit `linux/amd64` locks
- committed x86_64 ELF binaries
- curated known x86-only dependencies
- curated ARM64-safe base images

Only ambiguous findings are sent to Bedrock.

## Why Bedrock receives evidence, not the whole repository

The model gets normalized scanner findings, package names and summaries. This
reduces token usage and reduces the chance that arbitrary repository text is
treated as instructions.

## QEMU

QEMU/binfmt is enabled inside the privileged CodeBuild environment so an x86
build worker can build/test ARM64 images. It is **not** assumed to be available
as a transparent runtime emulator inside Fargate.

## Shared ECS service

The MVP keeps one ECS service and one ALB so every job replaces the previous
live demo application. This avoids provisioning a full ALB/ECS stack for every
repository and keeps the event infrastructure cheap and predictable.
