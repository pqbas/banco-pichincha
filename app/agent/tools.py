"""Tools de los subagentes, con su schema de argumentos en Pydantic.

- Cada agente tiene una lista de tools permitidas, ejecutar() rechaza cualquier otra
- Los argumentos los arma el orquestador desde la solicitud validada, nunca el LLM
- Todas son de lectura, con un reintento si el servicio externo falla
"""

from collections.abc import Callable

from pydantic import BaseModel

from app import store
from app.schemas import (
    IdentityResult,
    NivelRiesgo,
    Politica,
    Producto,
    RiskMatch,
    RiskResult,
)
from app.services import observabilidad
from app.services.retrieval import Buscador

UMBRAL_CONFIANZA = 0.8
K_POLITICAS = 5


class ToolNoPermitida(Exception):
    """Un agente pidió una tool fuera de su lista."""


class ToolFallo(Exception):
    """El servicio detrás de la tool no respondió tras el reintento."""


# --- Argumentos ---


class VerifyIdentityArgs(BaseModel):
    document_id: str


class CheckRiskListsArgs(BaseModel):
    name: str
    document_id: str


class ClientData(BaseModel):
    """Solo lo que la tool necesita, sin PII."""

    risk_level: NivelRiesgo | None
    pep: bool


class PrepareDocumentationArgs(BaseModel):
    product: Producto
    client_data: ClientData


class SearchPoliciesArgs(BaseModel):
    query: str
    k: int = K_POLITICAS


# --- Implementaciones ---


def verify_identity(args: VerifyIdentityArgs) -> IdentityResult:
    verified, confidence = store.consultar_registro_civil(args.document_id)
    return IdentityResult(verified=verified, confidence=confidence)


def check_risk_lists(args: CheckRiskListsArgs) -> RiskResult:
    coincidencias = store.consultar_listas(args.name, args.document_id)
    matches = [
        RiskMatch(
            list_name=c["list_name"], match_type=c["match_type"], entry_id=c["entry_id"]
        )
        for c in coincidencias
    ]
    if any(
        m.list_name in store.LISTAS_SANCIONES and m.match_type == "documento"
        for m in matches
    ):
        nivel = "high"
    elif matches:
        nivel = "medium"
    else:
        nivel = "low"
    return RiskResult(risk_level=nivel, matches=matches)


def prepare_documentation(args: PrepareDocumentationArgs) -> list[str]:
    documentos = store.documentos_de(args.product)
    if args.client_data.risk_level in ("medium", "high"):
        documentos.append(store.DOCUMENTO_ORIGEN_FONDOS)
    if args.client_data.pep:
        documentos.append(store.DOCUMENTO_PEP)
    return documentos


_buscador = Buscador(store.POLITICAS, lambda p: f"{p.titulo} {p.texto}")


def search_policies(args: SearchPoliciesArgs) -> list[Politica]:
    return _buscador.buscar(args.query, args.k)


TOOLS: dict[str, tuple[type[BaseModel], Callable]] = {
    "verify_identity": (VerifyIdentityArgs, verify_identity),
    "check_risk_lists": (CheckRiskListsArgs, check_risk_lists),
    "prepare_documentation": (PrepareDocumentationArgs, prepare_documentation),
    "search_policies": (SearchPoliciesArgs, search_policies),
}

PERMISOS = {
    "identidad": {"verify_identity"},
    "riesgo": {"check_risk_lists"},
    "politicas": {"search_policies", "prepare_documentation"},
    "respuesta": set(),
}


def ejecutar(agente: str, tool: str, args: dict):
    """Valida permiso y argumentos, ejecuta con un reintento ante fallas del servicio externo.

    Cada llamada queda como observación tool en Langfuse, incluidas las rechazadas.
    """
    with observabilidad.observacion(tool, "tool", input=args, metadata={"agente": agente}) as obs:
        if tool not in PERMISOS.get(agente, set()):
            obs.update(level="ERROR", status_message="tool no permitida")
            raise ToolNoPermitida(f"{agente} no puede usar {tool}")
        schema, funcion = TOOLS[tool]
        argumentos = schema.model_validate(args)
        for intento in range(2):
            try:
                resultado = funcion(argumentos)
                obs.update(output=resultado, metadata={"agente": agente, "intentos": intento + 1})
                return resultado
            except (ConnectionError, TimeoutError) as e:
                if intento == 1:
                    obs.update(level="ERROR", status_message=str(e), metadata={"agente": agente, "intentos": 2})
                    raise ToolFallo(f"{tool}: {e}") from e
