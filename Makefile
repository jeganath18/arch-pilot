SHELL := /bin/bash

smoke:
	./scripts/smoke-test.sh

validate-sam:
	@if command -v sam >/dev/null 2>&1; then sam validate --lint --template template.yaml; else echo "SAM CLI not installed"; exit 1; fi

build:
	sam build

deploy:
	sam build && sam deploy --guided
