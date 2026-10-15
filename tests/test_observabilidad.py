import json

import pytest
from fastapi.testclient import TestClient
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import app
from app.routers.onboarding import obtener_llm
from app.schemas import MensajeCliente, SeleccionPoliticas
from app.services import observabilidad
from app.services.llm import MockLLM

URL = "/api/v1/onboarding/start"


@pytest.fixture(scope="module")
def langfuse_en_memoria():
    # Langfuse mantiene un cliente por public_key, así que va uno solo para todo el módulo
    exporter, scores = InMemorySpanExporter(), []
    cliente = Langfuse(public_key="pk-test", secret_key="sk-test", base_url="http://localhost:1",
                       span_exporter=exporter, mask=observabilidad.enmascarar)
    cliente.create_score = lambda **score: scores.append(score)  # los scores van por HTTP, no por OTel
    yield cliente, exporter, scores
    cliente.shutdown()


@pytest.fixture
def spans(langfuse_en_memoria):
    cliente, exporter, scores = langfuse_en_memoria
    observabilidad.configurar(cliente)
    llm = MockLLM(
        SeleccionPoliticas(policy_ids=["POL-01"], requisitos=["Verificar identidad"]),
        MensajeCliente(answer="Hola <NOMBRE>, tu cuenta fue aprobada.", decision_comunicada="aprobado", policy_ids=["POL-01"]),
    )
    app.dependency_overrides[obtener_llm] = lambda: llm

    def correr(document_id="1712345678", nombre="Juan Perez"):
        exporter.clear()
        scores.clear()
        body = TestClient(app).post(URL, json={"prospect_name": nombre, "document_id": document_id,
                                              "product": "cuenta_ahorros"}).json()
        cliente.flush()
        return body, exporter.get_finished_spans(), list(scores)

    yield correr
    app.dependency_overrides.clear()
    observabilidad.configurar(None)


def tipo(span):
    return span.attributes.get("langfuse.observation.type")


def test_una_traza_con_cada_agente_tool_guardrail_y_llm(spans):
    body, finalizados, scores = spans()
    assert body["trace_id"]
    assert {f"{s.context.trace_id:032x}" for s in finalizados} == {body["trace_id"]}
    nombres = {(s.name, tipo(s)) for s in finalizados}
    assert {("onboarding", "agent"), ("orquestador", "chain"), ("identidad", "agent"), ("riesgo", "agent"),
            ("politicas", "agent"), ("respuesta", "agent")} <= nombres
    assert {("verify_identity", "tool"), ("check_risk_lists", "tool"), ("search_policies", "tool"),
            ("prepare_documentation", "tool")} <= nombres
    assert {("escalamiento", "guardrail"), ("validar_seleccion", "guardrail"), ("validar_mensaje", "guardrail")} <= nombres
    assert {("llm:SeleccionPoliticas", "generation"), ("llm:MensajeCliente", "generation")} <= nombres
    assert {(sc["name"], sc["value"]) for sc in scores} == {("decision", "aprobado"), ("motivos_revision", 0)}


def test_las_tools_cuelgan_de_su_agente(spans):
    _, finalizados, _ = spans()
    por_id = {s.context.span_id: s for s in finalizados}
    padre = {s.name: por_id[s.parent.span_id].name for s in finalizados if s.parent and s.parent.span_id in por_id}
    assert padre["verify_identity"] == "identidad"
    assert padre["check_risk_lists"] == "riesgo"
    assert padre["llm:SeleccionPoliticas"] == "politicas"
    assert padre["identidad"] == "onboarding"


def test_ninguna_pii_sale_a_langfuse(spans):
    _, finalizados, _ = spans()
    exportado = json.dumps([dict(s.attributes) for s in finalizados], ensure_ascii=False)
    assert "1712345678" not in exportado
    assert "Juan" not in exportado
    assert "<DNI>" in exportado


def test_escalamiento_queda_como_warning(spans):
    _, finalizados, _ = spans(document_id="0912345678")
    guardrail = next(s for s in finalizados if s.name == "escalamiento")
    assert guardrail.attributes["langfuse.observation.level"] == "WARNING"
    assert "confianza_identidad_baja" in guardrail.attributes["langfuse.observation.output"]


def test_tool_caida_queda_como_error_con_reintento(spans):
    _, finalizados, _ = spans(document_id="1799999999")
    tool = next(s for s in finalizados if s.name == "verify_identity")
    assert tool.attributes["langfuse.observation.level"] == "ERROR"
    assert tool.attributes["langfuse.observation.metadata.intentos"] in (2, "2")


def test_sin_keys_no_rompe(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    observabilidad.configurar(None)
    assert observabilidad.cliente() is None
    with observabilidad.traza("x") as raiz, observabilidad.observacion("y", "tool") as obs:
        obs.update(output=1)
        assert raiz.trace_id is None
