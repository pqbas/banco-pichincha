"""Modelos de entrada y salida (IO) de la API y de la respuesta estructurada del LLM."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Producto = Literal["cuenta_ahorros", "cuenta_corriente", "tarjeta_credito", "credito_consumo"]
NivelRiesgo = Literal["low", "medium", "high"]
Decision = Literal["aprobado", "revision_humana"]

# Solo letras, espacios, apóstrofo y guion: el nombre es el único texto libre de la entrada
PATRON_NOMBRE = r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]+$"


# --- Entrada de la API ---


class OnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prospect_name: str = Field(min_length=3, max_length=100, pattern=PATRON_NOMBRE)
    document_id: str = Field(pattern=r"^\d{10}$", description="Cédula de Ecuador, 10 dígitos")
    product: Producto


# --- Resultados de las tools ---


class IdentityResult(BaseModel):
    verified: bool
    confidence: float = Field(ge=0, le=1)


class RiskMatch(BaseModel):
    list_name: str
    match_type: Literal["documento", "nombre"]
    entry_id: str


class RiskResult(BaseModel):
    risk_level: NivelRiesgo
    matches: list[RiskMatch]


class Politica(BaseModel):
    id: str
    titulo: str
    texto: str


# --- Lo que decide el LLM ---


class SeleccionPoliticas(BaseModel):
    """Agente de políticas: qué políticas recuperadas aplican y qué exigen."""

    policy_ids: list[str] = Field(description="ids de las políticas de <politicas> que aplican al caso")
    requisitos: list[str] = Field(description="obligaciones concretas para este onboarding, una por item")


class MensajeCliente(BaseModel):
    """Agente de respuesta: mensaje para el prospecto."""

    answer: str = Field(description="mensaje al prospecto en español, empieza con 'Hola <NOMBRE>,'")
    decision_comunicada: Decision = Field(description="la decisión que comunica el mensaje")
    policy_ids: list[str] = Field(description="ids de las políticas en que se basa el mensaje")


# --- Salida de la API ---


class PasoTraza(BaseModel):
    agente: str
    tool: str | None = None
    resultado: str


class UsoTokens(BaseModel):
    tokens_entrada: int
    tokens_salida: int


class OnboardingResponse(BaseModel):
    onboarding_id: str
    trace_id: str | None = Field(default=None, description="traza en Langfuse, None si está apagado")
    decision: Decision
    review_reasons: list[str]
    identity: IdentityResult | None = Field(description="None si la tool falló")
    risk: RiskResult | None = Field(description="None si la tool falló")
    policies: list[Politica] = Field(description="políticas citadas, evidencia")
    retrieved_policy_ids: list[str]
    requirements: list[str]
    required_documents: list[str]
    answer: str
    trace: list[PasoTraza]
    uso: UsoTokens


class OnboardingRecord(BaseModel):
    """Lo que se guarda en la base, sin PII: el documento va como hash."""

    onboarding_id: str
    trace_id: str | None = None
    created_at: str
    document_hash: str
    product: Producto
    decision: Decision
    review_reasons: list[str]
    identity: IdentityResult | None
    risk: RiskResult | None
    policy_ids: list[str]
    required_documents: list[str]
