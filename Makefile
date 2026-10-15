# make <objetivo> [VARIABLE=valor], por ejemplo: make eval REPETICIONES=3
CDK := npx -y aws-cdk@2.1145.0
REPETICIONES ?= 1
PORT ?= 8000
EVAL_ENDPOINT ?=

.PHONY: help install test run eval build deploy deploy-cdn destroy

help:
	@echo "make install                 uv sync con todos los grupos (dev y eval)"
	@echo "make test                    tests"
	@echo "make run [PORT=8000]         API local, front en /ui"
	@echo "make eval [REPETICIONES=1] [EVAL_ENDPOINT=url]   evaluación contra eval/golden.jsonl"
	@echo "make deploy                  build de la Lambda, deploy con CDK y carga de secretos desde .env"
	@echo "make deploy-cdn              igual, con CloudFront (requiere cuenta verificada)"
	@echo "make destroy                 borra el stack"

install:
	uv sync --all-groups

test:
	uv run pytest -q

run:
	[ -f .env ] || cp .env.example .env
	uv run uvicorn app.main:app --reload --port $(PORT)

eval:
	EVAL_ENDPOINT=$(EVAL_ENDPOINT) uv run --group eval python -W ignore -m eval.evaluar --repeticiones $(REPETICIONES)

build:
	./scripts/build_lambda.sh

deploy: build
	cd infra && $(CDK) deploy --require-approval never --outputs-file outputs.json
	./scripts/cargar_secretos.sh

deploy-cdn: build
	cd infra && $(CDK) deploy -c cdn=true --require-approval never --outputs-file outputs.json
	./scripts/cargar_secretos.sh

destroy:
	cd infra && $(CDK) destroy --force
