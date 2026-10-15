import logging

from fastapi import APIRouter, Depends, HTTPException

from app import store
from app.agent.workflow import iniciar_onboarding
from app.schemas import MensajeCliente, OnboardingRecord, OnboardingRequest, OnboardingResponse, SeleccionPoliticas
from app.services.llm import LLMClient, crear_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# Respuestas del mock para levantar la app sin red (LLM_PROVIDER=mock)
MOCK_RESPUESTAS = (
    SeleccionPoliticas(policy_ids=["POL-01"], requisitos=["Verificar identidad contra el Registro Civil"]),
    MensajeCliente(
        answer="Hola <NOMBRE>, tu solicitud fue aprobada. Para activar tu producto presenta los documentos indicados.",
        decision_comunicada="aprobado",
        policy_ids=["POL-01"],
    ),
)


def obtener_llm() -> LLMClient:
    return crear_llm(*MOCK_RESPUESTAS)


@router.post("/start", response_model=OnboardingResponse)
def start(solicitud: OnboardingRequest, llm: LLMClient = Depends(obtener_llm)) -> OnboardingResponse:
    try:
        return iniciar_onboarding(solicitud, llm)
    except Exception:
        # Fallas del proveedor del LLM o de la base, sin la solicitud en el log por la PII
        logger.exception("onboarding falló")
        raise HTTPException(503, "no se pudo completar el onboarding, intenta de nuevo")


@router.get("/{onboarding_id}", response_model=OnboardingRecord)
def obtener(onboarding_id: str) -> OnboardingRecord:
    registro = store.obtener_onboarding(onboarding_id)
    if registro is None:
        raise HTTPException(404, "onboarding no encontrado")
    return registro
