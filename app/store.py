"""Datos del servicio. En el prototipo son diccionarios en memoria, detrás de funciones para
poder cambiarlos por una base de datos o una API sin tocar los routers.

- Registro civil y listas de riesgo: APIs externas, mockeadas
- Catálogo de documentos por producto y políticas del banco
- Onboardings registrados: en memoria, o en DynamoDB si existe ONBOARDING_TABLE
"""

import json
import os
import unicodedata

from app.schemas import OnboardingRecord, Politica

# --- API externa: registro civil (mock) ---

# document_id -> (verified, confidence)
REGISTRO_CIVIL = {
    "1712345678": (True, 0.95),
    "0912345678": (True, 0.72),  # foto o datos parciales, confianza bajo umbral
    "1798765432": (False, 0.30),
    "1700000001": (True, 0.97),
    "1700000002": (True, 0.93),
    "1700000003": (True, 0.91),
}

# Documento que simula una caída del servicio externo
DOCUMENTO_CAIDA = "1799999999"


def consultar_registro_civil(document_id: str) -> tuple[bool, float]:
    if document_id == DOCUMENTO_CAIDA:
        raise ConnectionError("registro civil no disponible")
    return REGISTRO_CIVIL.get(document_id, (False, 0.0))


# --- API externa: listas de riesgo (mock) ---

LISTAS_RIESGO = [
    {"entry_id": "OFAC-0012", "list_name": "OFAC", "document_id": "1700000001", "nombre": "Carlos Mendoza Ruiz"},
    {"entry_id": "PEP-0107", "list_name": "PEP", "document_id": "1700000002", "nombre": "Maria Fernanda Lopez"},
    {"entry_id": "ONU-0333", "list_name": "ONU", "document_id": None, "nombre": "Pedro Ramirez Vera"},
]
LISTAS_SANCIONES = {"OFAC", "ONU", "UAFE"}


def normalizar_nombre(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", nombre.lower()).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.split())


def consultar_listas(nombre: str, document_id: str) -> list[dict]:
    if document_id == DOCUMENTO_CAIDA:
        raise ConnectionError("servicio de listas no disponible")
    nombre = normalizar_nombre(nombre)
    coincidencias = []
    for entrada in LISTAS_RIESGO:
        if entrada["document_id"] == document_id:
            coincidencias.append({**entrada, "match_type": "documento"})
        elif normalizar_nombre(entrada["nombre"]) == nombre:
            coincidencias.append({**entrada, "match_type": "nombre"})
    return coincidencias


# --- Catálogo de documentos por producto ---

DOCUMENTOS_PRODUCTO = {
    "cuenta_ahorros": ["Cédula de identidad vigente", "Planilla de servicio básico (máximo 3 meses)"],
    "cuenta_corriente": [
        "Cédula de identidad vigente",
        "Planilla de servicio básico (máximo 3 meses)",
        "Dos referencias bancarias o comerciales",
        "RUC, si tiene actividad económica independiente",
    ],
    "tarjeta_credito": [
        "Cédula de identidad vigente",
        "Rol de pagos o declaración de impuestos de los últimos 3 meses",
        "Autorización de consulta al buró de crédito",
    ],
    "credito_consumo": [
        "Cédula de identidad vigente",
        "Rol de pagos o declaración de impuestos de los últimos 3 meses",
        "Autorización de consulta al buró de crédito",
        "Detalle del destino del crédito",
    ],
}
DOCUMENTO_ORIGEN_FONDOS = "Declaración de licitud y origen de fondos"
DOCUMENTO_PEP = "Formulario de Persona Expuesta Políticamente y aprobación de gerencia"


def documentos_de(producto: str) -> list[str]:
    return list(DOCUMENTOS_PRODUCTO[producto])


# --- Políticas del banco (corpus del retrieval) ---

POLITICAS = [
    Politica(id="POL-01", titulo="Conozca a su cliente (KYC)", texto=(
        "Toda vinculación requiere verificar la identidad del prospecto contra el Registro Civil. "
        "Si la verificación falla o la confianza es menor a 0.8, el onboarding pasa a revisión humana "
        "y no se abre ningún producto hasta completarla.")),
    Politica(id="POL-02", titulo="Listas restrictivas y sanciones", texto=(
        "Antes de vincular se consultan las listas OFAC, ONU y UAFE. Una coincidencia por documento en una "
        "lista de sanciones es riesgo alto: no se vincula y se escala a Cumplimiento. Una coincidencia solo "
        "por nombre puede ser homónimo y se trata como riesgo medio. Está prohibido informar al prospecto "
        "que figura o podría figurar en una lista.")),
    Politica(id="POL-03", titulo="Personas Expuestas Políticamente (PEP)", texto=(
        "Un prospecto PEP requiere debida diligencia reforzada: formulario PEP, declaración de origen de "
        "fondos y aprobación de gerencia antes de activar el producto.")),
    Politica(id="POL-04", titulo="Cuenta de ahorros", texto=(
        "La cuenta de ahorros requiere cédula vigente y planilla de servicio básico de máximo tres meses. "
        "No exige depósito inicial mínimo y se puede abrir en canales digitales.")),
    Politica(id="POL-05", titulo="Cuenta corriente", texto=(
        "La cuenta corriente requiere cédula vigente, planilla de servicio básico, dos referencias bancarias "
        "o comerciales y RUC si el prospecto tiene actividad económica independiente.")),
    Politica(id="POL-06", titulo="Tarjeta de crédito", texto=(
        "La tarjeta de crédito requiere sustento de ingresos de los últimos tres meses y autorización de "
        "consulta al buró de crédito. El cupo se asigna según la capacidad de pago.")),
    Politica(id="POL-07", titulo="Crédito de consumo", texto=(
        "El crédito de consumo requiere sustento de ingresos, autorización de consulta al buró de crédito y "
        "el detalle del destino del crédito. La cuota no puede superar el 40% del ingreso neto.")),
    Politica(id="POL-08", titulo="Protección de datos personales", texto=(
        "Conforme a la Ley Orgánica de Protección de Datos Personales, se pide el consentimiento del "
        "prospecto, se recolectan solo los datos necesarios y no se exponen datos personales en mensajes "
        "ni registros.")),
    Politica(id="POL-09", titulo="Escalamiento a revisión humana", texto=(
        "Un analista de onboarding revisa los casos escalados en un plazo máximo de 24 horas hábiles. Al "
        "prospecto se le informa que su solicitud está en revisión, sin detallar el motivo.")),
    Politica(id="POL-10", titulo="Origen de fondos", texto=(
        "Los prospectos con riesgo medio presentan una declaración de licitud y origen de fondos antes de "
        "activar el producto.")),
]
POLITICAS_POR_ID = {p.id: p for p in POLITICAS}


# --- Onboardings registrados ---

_ONBOARDINGS: dict[str, OnboardingRecord] = {}


def _tabla():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["ONBOARDING_TABLE"])


def guardar_onboarding(registro: OnboardingRecord) -> None:
    if os.getenv("ONBOARDING_TABLE"):
        _tabla().put_item(Item={
            "onboarding_id": registro.onboarding_id,
            "created_at": registro.created_at,
            "decision": registro.decision,
            "data": registro.model_dump_json(),
        })
    else:
        _ONBOARDINGS[registro.onboarding_id] = registro


def obtener_onboarding(onboarding_id: str) -> OnboardingRecord | None:
    if os.getenv("ONBOARDING_TABLE"):
        item = _tabla().get_item(Key={"onboarding_id": onboarding_id}).get("Item")
        return OnboardingRecord.model_validate(json.loads(item["data"])) if item else None
    return _ONBOARDINGS.get(onboarding_id)
