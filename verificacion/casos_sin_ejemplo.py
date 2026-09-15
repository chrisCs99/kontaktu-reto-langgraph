"""Verificacion manual de los 3 casos del catalogo marcados con ⚠ en casos.md
que NO tienen evento de ejemplo en eventos/ (rechazada, callback fuera de la
ventana de llamadas, descartado), pero que casos.md dice que "aparecen en el
lote con el que evaluamos. Implementalos igual."

Esto no es una suite de tests formal (el enunciado no la pide) -- son tres
eventos sinteticos construidos a mano, ejecutados contra el grafo real, para
comprobar con los ojos que estos tres casos hacen lo que casos.md describe
antes de darlos por buenos. No escribe nada bajo eventos/ (esa carpeta no se
toca); solo genera los eventos en memoria.

Los dos casos que dependen de leer la transcripcion (callback fuera de
ventana, descartado) usan una clasificacion LLM fija en vez de llamar a la
API real, para poder correr esto sin gastar la clave de OpenAI. El caso
`rechazada` es puramente deterministico (sip_status_code=603) y no pasa por
el LLM en absoluto.

Uso (desde la raiz del repo, para que el paquete kontaktu se resuelva):

    python -m verificacion.casos_sin_ejemplo
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import kontaktu.graph as graph_mod
from kontaktu.config import cargar_campana
from kontaktu.models import ClasificacionLLM
from kontaktu.store import KontaktuStore

BASE = json.loads(Path("eventos/03-call-ended-elena.json").read_text(encoding="utf-8"))


def _evento(event_id: str, contact_id: str, **overrides) -> dict:
    evento = copy.deepcopy(BASE)
    evento["event_id"] = event_id
    evento["idempotency_key"] = f"verificacion-{event_id}"
    evento["lead"]["contact_id"] = contact_id
    evento["telephony"]["call_id"] = f"verificacion-{event_id}"
    for clave, valor in overrides.items():
        evento[clave] = valor
    return evento


# Caso 11 ⚠ rechazada: 603, rechazo activo antes de descolgar.
# Esperado (casos.md): canal de respaldo, SIN reintento por voz.
EVT_RECHAZADA = _evento(
    "test_rechazada", "c_test_rechazada",
    transcript=[],
    agent_outcome={"call_outcome": "no_answer", "reason": None, "appointment": None, "slots_snapshot": {}},
)
EVT_RECHAZADA["telephony"].update(
    sip_status_code=603, sip_status="Decline", disconnect_reason="USER_REJECTED",
    answered_at=None, duration_seconds=0,
)

# Caso 12 ⚠ callback fuera de ventana: el lead pide una hora que cae fuera de
# la ventana de llamadas (martes 21:00, pide "esta misma noche a las once").
# Esperado (casos.md): llamada en la primera franja valida + aviso_cambio_hora.
EVT_CALLBACK_FUERA = _evento(
    "test_callback_fuera", "c_test_callback",
    occurred_at="2026-09-15T21:00:00+02:00",
    agent_outcome={
        "call_outcome": "callback_requested", "reason": None, "appointment": None,
        "slots_snapshot": {"callback_when_raw": "esta misma noche a las once"},
    },
)
EVT_CALLBACK_FUERA["telephony"].update(
    sip_status_code=200, disconnect_reason="CLIENT_INITIATED", answered_at="2026-09-15T20:59:40+02:00",
    amd={"result": "human", "greeting_transcript": "Si", "detected_at_secs": 1.0, "source": "livekit_amd"},
)

# Caso 15 ⚠ descartado: el lead ya alquilo/compro y ya no busca.
# Esperado (casos.md): ninguna orden mas alla de cerrar la llamada.
EVT_DESCARTADO = _evento(
    "test_descartado", "c_test_descartado",
    agent_outcome={"call_outcome": "completed", "reason": None, "appointment": None, "slots_snapshot": {}},
    transcript=[
        {"role": "agent", "message": "Hola, te llamo por tu consulta sobre el piso.", "time_in_call_secs": 2},
        {"role": "user", "message": "Ya lo he alquilado hace dos semanas, ya no lo necesito.", "time_in_call_secs": 8},
    ],
)
EVT_DESCARTADO["telephony"].update(
    sip_status_code=200, disconnect_reason="CLIENT_INITIATED", answered_at="2026-09-15T11:03:41+02:00",
    amd={"result": "human", "greeting_transcript": "Si", "detected_at_secs": 1.0, "source": "livekit_amd"},
)

CLASIFICACIONES_FIJAS = {
    "test_callback_fuera": ClasificacionLLM(
        etiqueta="callback", motivo="el lead pide que le llamen esta misma noche a las once",
        confianza=0.85, nota_contexto="", callback_iso="2026-09-15T23:00:00+02:00",
    ),
    "test_descartado": ClasificacionLLM(
        etiqueta="descartado", motivo="el lead ya alquilo el inmueble y ya no lo busca", confianza=0.95,
    ),
}


def clasificar_fijo(evento: dict) -> ClasificacionLLM:
    return CLASIFICACIONES_FIJAS[evento["event_id"]]


def main() -> None:
    graph_mod.clasificar_con_llm = clasificar_fijo  # evita llamar a la API real

    campana = cargar_campana("config/campana.yaml")
    store = KontaktuStore("estado/verificacion_casos_sin_ejemplo.sqlite3")
    grafo = graph_mod.construir_grafo(campana, store)

    for evento in (EVT_RECHAZADA, EVT_CALLBACK_FUERA, EVT_DESCARTADO):
        store.begin()
        grafo.invoke({"evento": evento})
        store.commit()
    store.close()

    print("Listo. Revisa salida/decisiones.jsonl y salida/ordenes.jsonl para")
    print("los event_id test_rechazada, test_callback_fuera y test_descartado.")
    print("Esperado: rechazada -> solo whatsapp (sin reintento); callback_fuera")
    print("-> reintento al dia siguiente 10:00 + aviso_cambio_hora; descartado")
    print("-> solo cerrar_llamada.")


if __name__ == "__main__":
    main()
