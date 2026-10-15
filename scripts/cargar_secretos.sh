#!/usr/bin/env bash
# Copia a Secrets Manager el valor de cada secreto definido en infra/config.json, tomándolo del .env
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
python3 -c "import json; [print(k, v) for k, v in json.load(open('infra/config.json')).get('secretos', {}).items()]" |
while read -r variable nombre; do
  valor="${!variable:-}"
  if [ -z "$valor" ]; then echo "  $variable vacío en .env, se omite"; continue; fi
  aws secretsmanager put-secret-value --secret-id "$nombre" --secret-string "$valor" --query Name --output text
done
