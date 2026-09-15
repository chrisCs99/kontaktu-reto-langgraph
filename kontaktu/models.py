"""Modelos Pydantic: catalogo cerrado, entrada validada y salida de clasificacion del LLM."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ETIQUETAS_CATALOGO = [
    "visita_reservada", "documentacion_enviada", "callback", "sin_respuesta", "ocupado",
    "buzon", "cortada", "visita_sin_confirmar", "persona_equivocada", "no_contactar",
    "rechazada", "documentacion_pendiente", "descartado", "otro",
]
Etiqueta = Literal[
    "visita_reservada", "documentacion_enviada", "callback", "sin_respuesta", "ocupado",
    "buzon", "cortada", "visita_sin_confirmar", "persona_equivocada", "no_contactar",
    "rechazada", "documentacion_pendiente", "descartado", "otro",
]
EtiquetaODecision = Literal[
    "visita_reservada", "documentacion_enviada", "callback", "sin_respuesta", "ocupado",
    "buzon", "cortada", "visita_sin_confirmar", "persona_equivocada", "no_contactar",
    "rechazada", "documentacion_pendiente", "descartado", "otro", "no_aplica",
]

# Estado de cola que lleva cerrar_llamada, fijado por la etiqueta (casos.md).
ESTADO_POR_ETIQUETA: dict[str, str] = {
    "visita_reservada": "successful",
    "documentacion_enviada": "completed",
    "documentacion_pendiente": "completed",
    "callback": "callback_requested",
    "sin_respuesta": "no_answer",
    "ocupado": "no_answer",
    "buzon": "no_answer",
    "cortada": "needs_review",
    "visita_sin_confirmar": "needs_review",
    "otro": "needs_review",
    "persona_equivocada": "failed",
    "no_contactar": "dnc",
    "rechazada": "refused",
    "descartado": "skipped",
}


class ClasificacionLLM(BaseModel):
    """Salida estructurada del nodo LLM. Solo se invoca cuando la señalizacion por si
    sola no resuelve el caso (ver kontaktu/graph.py:clasificar_determinista)."""

    etiqueta: Etiqueta
    motivo: str = Field(description="Una frase, en español, que justifica la etiqueta.")
    confianza: float = Field(ge=0.0, le=1.0)
    nota_contexto: str = Field(
        default="",
        description="Resumen breve de lo ya hablado/recogido, para no repetir preguntas en el siguiente intento.",
    )
    callback_iso: Optional[str] = Field(
        default=None,
        description="Solo si etiqueta=callback: instante solicitado por el lead, en ISO 8601 con offset de Europe/Madrid.",
    )


class Decision(BaseModel):
    event_id: str
    call_id: Optional[str] = None
    etiqueta: EtiquetaODecision
    motivo: str
    confianza: float = Field(ge=0.0, le=1.0)
    ordenes: list[str] = Field(default_factory=list)


class Orden(BaseModel):
    orden_id: str
    event_id: str
    operacion: str
    idempotency_key: str
    cuerpo: dict
