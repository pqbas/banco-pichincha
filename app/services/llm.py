"""Cliente de LLM con salida estructurada, intercambiable entre mock, Anthropic y Bedrock.

Todas las implementaciones exponen estructurado(system, prompt, schema) -> instancia de schema validada.
"""

import functools
import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.services import observabilidad

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """El modelo no devolvió una respuesta utilizable."""


class Uso:
    """Tokens acumulados, para reportar costo por request y en la evaluación."""

    def __init__(self) -> None:
        self.tokens_entrada = 0
        self.tokens_salida = 0

    def sumar(self, entrada: int, salida: int) -> None:
        self.tokens_entrada += entrada
        self.tokens_salida += salida


def _generacion(metodo):
    """Registra cada llamada como generation en Langfuse: modelo, prompt, salida y tokens."""

    @functools.wraps(metodo)
    def envoltura(self, system: str, prompt: str, schema: type[T]) -> T:
        antes = (self.uso.tokens_entrada, self.uso.tokens_salida)
        with observacion_llm(self, schema, system, prompt) as gen:
            try:
                resultado = metodo(self, system, prompt, schema)
            except LLMError as e:
                gen.update(level="ERROR", status_message=str(e))
                raise
            finally:
                gen.update(usage_details={
                    "input": self.uso.tokens_entrada - antes[0],
                    "output": self.uso.tokens_salida - antes[1],
                })
            gen.update(output=resultado.model_dump())
            return resultado

    return envoltura


def observacion_llm(llm, schema: type[BaseModel], system: str, prompt: str):
    return observabilidad.observacion(
        f"llm:{schema.__name__}",
        "generation",
        model=getattr(llm, "model", "mock"),
        input=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )


class LLMClient(Protocol):
    uso: Uso

    def estructurado(self, system: str, prompt: str, schema: type[T]) -> T: ...


class MockLLM:
    """Devuelve respuestas fijas por schema, para tests y para correr sin red ni costo."""

    def __init__(self, *respuestas: BaseModel):
        self.respuestas = {type(r): r for r in respuestas}
        self.ultimo_prompt: str | None = None
        self.uso = Uso()

    @_generacion
    def estructurado(self, system: str, prompt: str, schema: type[T]) -> T:
        self.ultimo_prompt = prompt
        if schema not in self.respuestas:
            raise LLMError(f"MockLLM sin respuesta para {schema.__name__}")
        return self.respuestas[schema]


class AnthropicLLM:
    """API de Anthropic, la salida llega validada contra el modelo Pydantic con messages.parse."""

    def __init__(self, model: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
        self.uso = Uso()

    @_generacion
    def estructurado(self, system: str, prompt: str, schema: type[T]) -> T:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        self.uso.sumar(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise LLMError(f"sin respuesta estructurada, stop_reason={response.stop_reason}")
        return response.parsed_output


class BedrockLLM:
    """Bedrock converse, forzando una tool cuyo input es el JSON Schema del modelo Pydantic."""

    def __init__(self, model: str | None = None, region: str | None = None):
        import boto3

        self.client = boto3.client("bedrock-runtime", region_name=region or os.getenv("AWS_REGION", "us-east-1"))
        self.model = model or os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.uso = Uso()

    @_generacion
    def estructurado(self, system: str, prompt: str, schema: type[T]) -> T:
        tool = f"registrar_{schema.__name__.lower()}"
        response = self.client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4000},
            toolConfig={
                "tools": [{
                    "toolSpec": {
                        "name": tool,
                        "description": f"Registra la respuesta como {schema.__name__}",
                        "inputSchema": {"json": schema.model_json_schema()},
                    }
                }],
                "toolChoice": {"tool": {"name": tool}},
            },
        )
        uso = response.get("usage", {})
        self.uso.sumar(uso.get("inputTokens", 0), uso.get("outputTokens", 0))
        for bloque in response["output"]["message"]["content"]:
            if "toolUse" in bloque:
                return schema.model_validate(bloque["toolUse"]["input"])
        raise LLMError(f"sin toolUse, stopReason={response['stopReason']}")


def crear_llm(*respuestas_mock: BaseModel) -> LLMClient:
    """LLM_PROVIDER elige la implementación: mock (por defecto), anthropic o bedrock."""
    proveedor = os.getenv("LLM_PROVIDER", "mock")
    if proveedor == "anthropic":
        return AnthropicLLM()
    if proveedor == "bedrock":
        return BedrockLLM()
    return MockLLM(*respuestas_mock)
