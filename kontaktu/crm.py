"""Construccion de ordenes CRM: las 7 operaciones de esquemas/crm-openapi.yaml.

No hay servidor real (enunciado, seccion 4.2): cada orden es la peticion que se
habria hecho. Los identificadores que "devolveria" el CRM (reminder_id, task_id...)
los generamos nosotros de forma deterministica -- hash de la idempotency_key de la
propia orden -- para poder persistirlos y reutilizarlos mas tarde (p.ej. cancelar un
recordatorio creado en un proceso anterior).
"""
from __future__ import annotations

import hashlib


def _hash_id(prefijo: str, semilla: str) -> str:
    return f"{prefijo}_{hashlib.sha1(semilla.encode('utf-8')).hexdigest()[:8]}"


def orden_id(idempotency_key_orden: str) -> str:
    return _hash_id("ord", idempotency_key_orden)


def reminder_id(idempotency_key_orden: str) -> str:
    return _hash_id("rem", idempotency_key_orden)


def clave_orden(idempotency_key_evento: str, operacion: str, distintivo: str | None = None) -> str:
    """<idempotency_key del evento>:<operacion>[:<distintivo>], per crm-openapi.yaml."""
    base = f"{idempotency_key_evento}:{operacion}"
    return f"{base}:{distintivo}" if distintivo else base


def construir_orden(event_id: str, operacion: str, idempotency_key_evento: str, cuerpo: dict,
                     distintivo: str | None = None) -> dict:
    idem = clave_orden(idempotency_key_evento, operacion, distintivo)
    return {
        "orden_id": orden_id(idem),
        "event_id": event_id,
        "operacion": operacion,
        "idempotency_key": idem,
        "cuerpo": cuerpo,
    }
