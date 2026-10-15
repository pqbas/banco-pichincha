"""Trazas en Langfuse: una traza por request con una observación por agente, tool, guardrail y
llamada al LLM. Sin dominio, cualquier caso la usa igual.

- Se activa con LANGFUSE_PUBLIC_KEY y LANGFUSE_SECRET_KEY (LANGFUSE_BASE_URL para self-hosted),
  sin ellas todo es no-op y la app corre igual
- Todo lo que sale pasa por enmascarar(): llaves con PII se reemplazan y los textos pasan por
  enmascarar_pii, como defensa aunque los llamadores ya manden datos sin PII
- En Lambda hay que llamar flush() al final de cada invocación (lo hace lambda_handler)
"""

import json
import os
from contextlib import contextmanager, nullcontext
from typing import Any

from app.services.pii import enmascarar_pii

# Llaves cuyo valor nunca sale a Langfuse, con su marcador
LLAVES_PII = {"prospect_name": "<NOMBRE>", "name": "<NOMBRE>", "document_id": "<DNI>"}

_cliente = None
_configurado = False


def _serializable(valor: Any) -> Any:
    return json.loads(json.dumps(valor, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


def _redactar(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: LLAVES_PII[k] if k in LLAVES_PII else _redactar(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_redactar(v) for v in valor]
    if isinstance(valor, str):
        return enmascarar_pii(valor)
    return valor


def enmascarar(*, data: Any, **_: Any) -> Any:
    return _redactar(_serializable(data))


def configurar(cliente=None) -> None:
    """Fija el cliente, None lo crea desde el entorno. Los tests pasan uno con un exporter en memoria."""
    global _cliente, _configurado
    if cliente is None and os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        from langfuse import Langfuse, is_langfuse_span

        # Solo spans propios: el SDK de anthropic trae su instrumentación OTel y duplicaría
        # cada llamada (y sus tokens) junto a la generation que ya registra llm.py
        cliente = Langfuse(mask=enmascarar, should_export_span=is_langfuse_span)
    _cliente, _configurado = cliente, True


def cliente():
    if not _configurado:
        configurar()
    return _cliente


class _Nada:
    """Observación no-op para cuando Langfuse no está configurado."""

    id = trace_id = None

    def update(self, **_: Any) -> "_Nada":
        return self

    def score_trace(self, **_: Any) -> None:
        pass


def observacion(nombre: str, tipo: str = "span", **campos: Any):
    """Context manager con la observación (span, agent, tool, generation, guardrail...)."""
    lf = cliente()
    if lf is None:
        return nullcontext(_Nada())
    return lf.start_as_current_observation(name=nombre, as_type=tipo, **campos)


@contextmanager
def traza(nombre: str, *, session_id: str | None = None, tags: list[str] | None = None,
          metadata: dict[str, str] | None = None, **campos: Any):
    """Observación raíz con los atributos de traza propagados a todas las observaciones hijas."""
    if cliente() is None:
        yield _Nada()
        return
    from langfuse import propagate_attributes

    with propagate_attributes(trace_name=nombre, session_id=session_id, tags=tags, metadata=metadata):
        with observacion(nombre, "agent", **campos) as raiz:
            yield raiz


def url_traza(trace_id: str | None) -> str | None:
    lf = cliente()
    return lf.get_trace_url(trace_id=trace_id) if lf and trace_id else None


def flush() -> None:
    lf = cliente()
    if lf is not None:
        lf.flush()
