"""Onboarding multi agente con LangGraph, sin nada de HTTP.

Orquestador (código) -> identidad -> riesgo -> políticas (LLM) -> respuesta (LLM).

- El orquestador decide el siguiente agente, le pasa solo los datos que necesita y aplica las
  reglas de escalamiento. El orden es fijo para que ningún control se pueda saltar.
- Cada agente usa solo sus tools (tools.PERMISOS), con argumentos armados desde la solicitud.
- El LLM no recibe nombre ni documento, y su salida se valida contra lo que se le entregó.
- La decisión final la toma el código: revisión humana si hay cualquier motivo.
"""

import hashlib
import json
import logging
import operator
import uuid
from datetime import UTC, datetime
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app import store
from app.agent import guardrails, prompts
from app.agent.tools import ToolFallo, ejecutar
from app.schemas import (
    IdentityResult,
    MensajeCliente,
    OnboardingRecord,
    OnboardingRequest,
    OnboardingResponse,
    PasoTraza,
    Politica,
    RiskResult,
    SeleccionPoliticas,
    UsoTokens,
)
from app.services import observabilidad
from app.services.llm import LLMClient, LLMError

auditoria = logging.getLogger("auditoria")

AGENTES = ["identidad", "riesgo", "politicas", "respuesta"]

MENSAJE_REVISION = (
    "Hola <NOMBRE>, recibimos tu solicitud y está en revisión. Un analista te contactará en un "
    "plazo máximo de 24 horas hábiles para continuar con el proceso. Gracias por elegirnos."
)


class Estado(TypedDict, total=False):
    solicitud: OnboardingRequest
    completados: Annotated[list[str], operator.add]
    siguiente: str
    identidad: IdentityResult | None
    riesgo: RiskResult | None
    recuperadas: list[Politica]
    seleccion: SeleccionPoliticas | None
    documentos: list[str]
    mensaje: MensajeCliente | None
    motivos: Annotated[list[str], operator.add]
    traza: Annotated[list[PasoTraza], operator.add]


def decision_de(motivos: list[str]) -> str:
    return "revision_humana" if motivos else "aprobado"


# --- Observabilidad ---


def _vista(estado: dict) -> dict:
    """Estado sin la solicitud (PII) ni la traza, para registrar qué recibe cada agente."""
    vista = {k: v for k, v in estado.items() if k not in ("solicitud", "traza")}
    if "solicitud" in estado:
        vista["producto"] = estado["solicitud"].product
    return vista


def _observado(nombre: str, tipo: str, nodo):
    """Cada nodo del grafo queda como observación con su entrada y su salida."""

    def envoltura(estado: Estado) -> dict:
        with observabilidad.observacion(nombre, tipo, input=_vista(estado)) as obs:
            salida = nodo(estado)
            obs.update(output=_vista(salida))
            return salida

    return envoltura


def _guardrail(nombre: str, entrada, motivos: list[str]) -> None:
    with observabilidad.observacion(
        nombre, "guardrail", input=entrada, output=motivos,
        level="WARNING" if motivos else "DEFAULT",
    ):
        pass


# --- Orquestador ---


def orquestador(estado: Estado) -> dict:
    hechos = estado.get("completados", [])
    siguiente = next((a for a in AGENTES if a not in hechos), END)
    salida: dict = {"siguiente": siguiente}
    # Con identidad y riesgo listos, se aplican las reglas antes de seguir
    if siguiente == "politicas":
        motivos = guardrails.motivos_escalamiento(
            estado.get("identidad"), estado.get("riesgo")
        )
        _guardrail(
            "escalamiento",
            {"identidad": estado.get("identidad"), "riesgo": estado.get("riesgo")},
            motivos,
        )
        salida["motivos"] = motivos
        salida["traza"] = [
            PasoTraza(
                agente="orquestador", resultado=f"escalamiento: {motivos or 'ninguno'}"
            )
        ]
    return salida


# --- Subagentes de verificación (solo tools) ---


def agente_identidad(estado: Estado) -> dict:
    document_id = estado["solicitud"].document_id
    try:
        identidad = ejecutar(
            "identidad", "verify_identity", {"document_id": document_id}
        )
        resultado = (
            f"verified={identidad.verified} confidence={identidad.confidence:.2f}"
        )
    except ToolFallo as e:
        identidad, resultado = None, f"error: {e}"
    return {
        "identidad": identidad,
        "completados": ["identidad"],
        "traza": [
            PasoTraza(agente="identidad", tool="verify_identity", resultado=resultado)
        ],
    }


def agente_riesgo(estado: Estado) -> dict:
    solicitud = estado["solicitud"]
    try:
        riesgo = ejecutar(
            "riesgo",
            "check_risk_lists",
            {"name": solicitud.prospect_name, "document_id": solicitud.document_id},
        )
        resultado = f"risk_level={riesgo.risk_level} matches={[m.entry_id for m in riesgo.matches]}"
    except ToolFallo as e:
        riesgo, resultado = None, f"error: {e}"
    return {
        "riesgo": riesgo,
        "completados": ["riesgo"],
        "traza": [
            PasoTraza(agente="riesgo", tool="check_risk_lists", resultado=resultado)
        ],
    }


# --- Subagentes con LLM ---


def _consulta_politicas(
    producto: str, riesgo: RiskResult | None, pep: bool, escalado: bool = False
) -> str:
    consulta = (
        f"{producto.replace('_', ' ')} requisitos verificación identidad vinculación"
    )
    if escalado:
        consulta += " escalamiento revisión humana analista"
    if riesgo and riesgo.matches:
        consulta += " listas restrictivas coincidencia origen de fondos"
    if pep:
        consulta += " persona expuesta políticamente"
    return consulta


def crear_agente_politicas(llm: LLMClient):
    def agente_politicas(estado: Estado) -> dict:
        producto = estado["solicitud"].product
        riesgo = estado.get("riesgo")
        pep = bool(riesgo and any(m.list_name == "PEP" for m in riesgo.matches))
        recuperadas = ejecutar(
            "politicas",
            "search_policies",
            {
                "query": _consulta_politicas(
                    producto, riesgo, pep, escalado=bool(estado.get("motivos"))
                )
            },
        )
        documentos = ejecutar(
            "politicas",
            "prepare_documentation",
            {
                "product": producto,
                "client_data": {
                    "risk_level": riesgo.risk_level if riesgo else None,
                    "pep": pep,
                },
            },
        )
        traza = [
            PasoTraza(
                agente="politicas",
                tool="search_policies",
                resultado=f"recuperadas={[p.id for p in recuperadas]}",
            ),
            PasoTraza(
                agente="politicas",
                tool="prepare_documentation",
                resultado=f"{len(documentos)} documentos",
            ),
        ]
        identidad = estado.get("identidad")
        caso = {
            "producto": producto,
            "identidad": identidad.model_dump() if identidad else "no disponible",
            "riesgo": (
                {
                    "risk_level": riesgo.risk_level,
                    "listas": [
                        f"{m.list_name} por {m.match_type}" for m in riesgo.matches
                    ],
                }
                if riesgo
                else "no disponible"
            ),
            "motivos_escalamiento": estado.get("motivos", []),
        }
        try:
            seleccion = llm.estructurado(
                prompts.SYSTEM_POLITICAS,
                prompts.prompt_politicas(caso, recuperadas),
                SeleccionPoliticas,
            )
            motivos = guardrails.validar_seleccion(
                seleccion, {p.id for p in recuperadas}
            )
            _guardrail(
                "validar_seleccion",
                {"citadas": seleccion.policy_ids, "recuperadas": [p.id for p in recuperadas]},
                motivos,
            )
            traza.append(
                PasoTraza(agente="politicas", resultado=f"cita={seleccion.policy_ids}")
            )
        except LLMError as e:
            seleccion, motivos = None, ["llm_no_disponible: politicas"]
            traza.append(PasoTraza(agente="politicas", resultado=f"error: {e}"))
        return {
            "recuperadas": recuperadas,
            "documentos": documentos,
            "seleccion": seleccion,
            "motivos": motivos,
            "completados": ["politicas"],
            "traza": traza,
        }

    return agente_politicas


def crear_agente_respuesta(llm: LLMClient):
    def agente_respuesta(estado: Estado) -> dict:
        decision = decision_de(estado.get("motivos", []))
        seleccion = estado.get("seleccion")
        recuperadas = estado.get("recuperadas", [])
        citadas = [p for p in recuperadas if seleccion and p.id in seleccion.policy_ids]
        prompt = prompts.prompt_respuesta(
            estado["solicitud"].product,
            decision,
            estado.get("documentos", []),
            seleccion.requisitos if seleccion else [],
            citadas or recuperadas,
        )
        try:
            mensaje = llm.estructurado(prompts.SYSTEM_RESPUESTA, prompt, MensajeCliente)
            motivos = guardrails.validar_mensaje(
                mensaje, decision, {p.id for p in recuperadas}
            )
            _guardrail(
                "validar_mensaje",
                {"decision": decision, "mensaje": mensaje},
                motivos,
            )
        except LLMError as e:
            mensaje, motivos = None, [f"llm_no_disponible: respuesta ({e})"]
        # Un mensaje que no pasa las validaciones no llega al cliente
        if motivos:
            mensaje = None
        resultado = f"decision={decision} motivos={motivos or 'ninguno'}"
        return {
            "mensaje": mensaje,
            "motivos": motivos,
            "completados": ["respuesta"],
            "traza": [PasoTraza(agente="respuesta", resultado=resultado)],
        }

    return agente_respuesta


def construir_grafo(llm: LLMClient):
    grafo = StateGraph(Estado)
    grafo.add_node("orquestador", _observado("orquestador", "chain", orquestador))
    grafo.add_node("identidad", _observado("identidad", "agent", agente_identidad))
    grafo.add_node("riesgo", _observado("riesgo", "agent", agente_riesgo))
    grafo.add_node("politicas", _observado("politicas", "agent", crear_agente_politicas(llm)))
    grafo.add_node("respuesta", _observado("respuesta", "agent", crear_agente_respuesta(llm)))
    grafo.add_edge(START, "orquestador")
    grafo.add_conditional_edges(
        "orquestador", lambda e: e["siguiente"], [*AGENTES, END]
    )
    for agente in AGENTES:
        grafo.add_edge(agente, "orquestador")
    return grafo.compile()


# --- Caso de uso ---


def iniciar_onboarding(
    solicitud: OnboardingRequest, llm: LLMClient
) -> OnboardingResponse:
    onboarding_id = str(uuid.uuid4())
    # Una traza por onboarding, si el grafo falla a mitad la excepción queda registrada en ella
    with observabilidad.traza(
        "onboarding",
        session_id=onboarding_id,
        tags=[solicitud.product],
        metadata={"onboarding_id": onboarding_id},
        input={"product": solicitud.product},
    ) as raiz:
        respuesta = _ejecutar(onboarding_id, raiz.trace_id, solicitud, llm)
        raiz.update(output={
            "decision": respuesta.decision,
            "review_reasons": respuesta.review_reasons,
            "policy_ids": [p.id for p in respuesta.policies],
        })
        raiz.score_trace(name="decision", value=respuesta.decision, data_type="CATEGORICAL")
        raiz.score_trace(name="motivos_revision", value=len(respuesta.review_reasons), data_type="NUMERIC")
    return respuesta


def _ejecutar(
    onboarding_id: str, trace_id: str | None, solicitud: OnboardingRequest, llm: LLMClient
) -> OnboardingResponse:
    estado = construir_grafo(llm).invoke(
        {"solicitud": solicitud, "completados": [], "motivos": [], "traza": []}
    )

    motivos = estado["motivos"]
    decision = decision_de(motivos)
    mensaje = estado.get("mensaje")
    texto = mensaje.answer if mensaje else MENSAJE_REVISION
    nombre = solicitud.prospect_name.split()[0].capitalize()
    seleccion = estado.get("seleccion")
    citadas = [
        p
        for p in estado.get("recuperadas", [])
        if seleccion and p.id in seleccion.policy_ids
    ]

    respuesta = OnboardingResponse(
        onboarding_id=onboarding_id,
        trace_id=trace_id,
        decision=decision,
        review_reasons=motivos,
        identity=estado.get("identidad"),
        risk=estado.get("riesgo"),
        policies=citadas,
        retrieved_policy_ids=[p.id for p in estado.get("recuperadas", [])],
        requirements=seleccion.requisitos if seleccion else [],
        required_documents=estado.get("documentos", []),
        answer=texto.replace("<NOMBRE>", nombre),
        trace=estado["traza"],
        uso=UsoTokens(
            tokens_entrada=llm.uso.tokens_entrada, tokens_salida=llm.uso.tokens_salida
        ),
    )
    registro = OnboardingRecord(
        onboarding_id=respuesta.onboarding_id,
        trace_id=trace_id,
        created_at=datetime.now(UTC).isoformat(),
        document_hash=hashlib.sha256(solicitud.document_id.encode()).hexdigest()[:16],
        product=solicitud.product,
        decision=decision,
        review_reasons=motivos,
        identity=respuesta.identity,
        risk=respuesta.risk,
        policy_ids=[p.id for p in citadas],
        required_documents=respuesta.required_documents,
    )
    store.guardar_onboarding(registro)
    # Auditoría sin PII: el registro ya trae el documento como hash y no lleva nombre
    auditoria.info(
        json.dumps(
            {
                "evento": "onboarding",
                **registro.model_dump(),
                "uso": respuesta.uso.model_dump(),
            },
            ensure_ascii=False,
        )
    )
    return respuesta
