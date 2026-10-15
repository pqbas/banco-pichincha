import pytest

from app import store
from app.agent import tools
from app.agent.tools import ToolFallo, ToolNoPermitida, ejecutar


@pytest.mark.parametrize("agente, tool", [
    ("identidad", "check_risk_lists"),
    ("riesgo", "verify_identity"),
    ("respuesta", "search_policies"),
    ("desconocido", "verify_identity"),
])
def test_agente_no_usa_tools_ajenas(agente, tool):
    with pytest.raises(ToolNoPermitida):
        ejecutar(agente, tool, {"document_id": "1712345678", "name": "x", "query": "x"})


def test_reintenta_una_vez(monkeypatch):
    llamadas = []

    def inestable(document_id):
        llamadas.append(document_id)
        if len(llamadas) == 1:
            raise TimeoutError("lento")
        return True, 0.9

    monkeypatch.setattr(store, "consultar_registro_civil", inestable)
    assert ejecutar("identidad", "verify_identity", {"document_id": "1712345678"}).confidence == 0.9
    assert len(llamadas) == 2


def test_falla_tras_reintento():
    with pytest.raises(ToolFallo):
        ejecutar("identidad", "verify_identity", {"document_id": store.DOCUMENTO_CAIDA})


def test_documentacion_por_riesgo():
    base = tools.prepare_documentation(tools.PrepareDocumentationArgs(
        product="cuenta_ahorros", client_data={"risk_level": "low", "pep": False}))
    medio = tools.prepare_documentation(tools.PrepareDocumentationArgs(
        product="cuenta_ahorros", client_data={"risk_level": "medium", "pep": True}))
    assert store.DOCUMENTO_ORIGEN_FONDOS not in base
    assert {store.DOCUMENTO_ORIGEN_FONDOS, store.DOCUMENTO_PEP} <= set(medio)


@pytest.mark.parametrize("producto, politica", [
    ("cuenta_ahorros", "POL-04"),
    ("cuenta_corriente", "POL-05"),
    ("tarjeta_credito", "POL-06"),
    ("credito_consumo", "POL-07"),
])
def test_retrieval_trae_kyc_y_la_politica_del_producto(producto, politica):
    from app.agent.workflow import _consulta_politicas

    ids = [p.id for p in tools.search_policies(tools.SearchPoliciesArgs(query=_consulta_politicas(producto, None, False)))]
    assert "POL-01" in ids and politica in ids
