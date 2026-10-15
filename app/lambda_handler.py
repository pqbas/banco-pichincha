"""Entrada de Lambda. Carga secretos de Secrets Manager en el cold start y expone FastAPI con Mangum.

SECRETS es un JSON {"NOMBRE_VARIABLE": "arn-del-secreto"} que define el stack de CDK.
"""

import json
import os

import boto3

for variable, arn in json.loads(os.getenv("SECRETS", "{}")).items():
    if not os.getenv(variable):
        os.environ[variable] = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402
from app.services import observabilidad  # noqa: E402

_mangum = Mangum(app, lifespan="off")


def handler(event, context):
    # Lambda congela el proceso al responder, las trazas pendientes se envían antes
    try:
        return _mangum(event, context)
    finally:
        observabilidad.flush()
