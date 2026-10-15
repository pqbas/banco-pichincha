#!/usr/bin/env bash
# Arma dist/lambda con las dependencias de producción del uv.lock, compiladas para Lambda
# (Linux x86_64 glibc 2.28+, Python 3.13 sobre Amazon Linux 2023), sin Docker
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist/lambda && mkdir -p dist/lambda
# boto3 ya viene en el runtime de Lambda
uv export --frozen --no-dev --no-hashes --no-emit-project \
  --no-emit-package boto3 --no-emit-package botocore --no-emit-package s3transfer > dist/requirements-lambda.txt
uv pip install --quiet --python-platform x86_64-manylinux_2_28 --python-version 3.13 \
  --only-binary=:all: --target dist/lambda -r dist/requirements-lambda.txt
cp -r app dist/lambda/app
find dist/lambda -name '__pycache__' -type d -prune -exec rm -rf {} +
[ -x infra/.venv/bin/python ] || { uv venv -q infra/.venv --python 3.13 && uv pip install -q -p infra/.venv/bin/python -r infra/requirements.txt; }
echo "dist/lambda listo ($(du -sh dist/lambda | cut -f1))"
