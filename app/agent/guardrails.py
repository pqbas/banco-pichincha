"""Validaciones de los resultados de las tools y de la salida del LLM.

- Escalamiento: reglas del orquestador sobre identidad y riesgo
- Salida: ids citados dentro de lo recuperado, decisión comunicada igual a la decidida,
  sin PII ni mención de listas en el mensaje al cliente (tipping-off)
- Devuelven los motivos para marcar revisión humana, no lanzan excepciones por dudas de
  negocio
"""

import re

from app.agent.tools import UMBRAL_CONFIANZA
from app.schemas import IdentityResult, MensajeCliente, RiskResult, SeleccionPoliticas
from app.services.pii import enmascarar_pii

PALABRAS_PROHIBIDAS = re.compile(
    r"\b(listas?\s+(restrictivas?|negras?)|sancion\w*|ofac|onu|uafe|pep|expuest[ao]s?\s+pol[ií]tic\w*|"
    r"lavado|riesgo\s+(alto|medio|bajo)|risk)\b",
    re.IGNORECASE,
)


def motivos_escalamiento(identidad: IdentityResult | None, riesgo: RiskResult | None) -> list[str]:
    motivos = []
    if identidad is None:
        motivos.append("verificacion_identidad_no_disponible")
    elif not identidad.verified:
        motivos.append("identidad_no_verificada")
    elif identidad.confidence < UMBRAL_CONFIANZA:
        motivos.append(f"confianza_identidad_baja ({identidad.confidence:.2f} < {UMBRAL_CONFIANZA})")
    if riesgo is None:
        motivos.append("consulta_listas_no_disponible")
    elif riesgo.risk_level == "high":
        motivos.append("riesgo_alto")
    return motivos


def validar_seleccion(seleccion: SeleccionPoliticas, recuperadas: set[str]) -> list[str]:
    motivos = []
    fuera = [i for i in seleccion.policy_ids if i not in recuperadas]
    if fuera:
        motivos.append(f"politicas_citadas_fuera_de_contexto: {', '.join(fuera)}")
    if not set(seleccion.policy_ids) & recuperadas:
        motivos.append("sin_politicas_aplicables")
    return motivos


def validar_mensaje(mensaje: MensajeCliente, decision: str, recuperadas: set[str]) -> list[str]:
    motivos = []
    if mensaje.decision_comunicada != decision:
        motivos.append("mensaje_inconsistente_con_decision")
    if enmascarar_pii(mensaje.answer) != mensaje.answer:
        motivos.append("mensaje_con_pii")
    if PALABRAS_PROHIBIDAS.search(mensaje.answer):
        motivos.append("mensaje_revela_verificaciones")
    fuera = [i for i in mensaje.policy_ids if i not in recuperadas]
    if fuera:
        motivos.append(f"mensaje_cita_politicas_fuera_de_contexto: {', '.join(fuera)}")
    return motivos
