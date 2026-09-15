#!/usr/bin/env python
"""Orquestador post-llamada. Uso: python run.py eventos/01-call-ended-nuria.json

Contrato (enunciado, seccion 2): un evento por invocacion, proceso nuevo cada vez,
salida por append en salida/{decisiones,ordenes}.jsonl, codigo de salida 0 si se
proceso el evento.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
from dotenv import load_dotenv

from kontaktu.config import cargar_campana
from kontaktu.graph import construir_grafo
from kontaktu.store import KontaktuStore

ESQUEMA_EVENTO = json.loads(Path("esquemas/evento.schema.json").read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: python run.py <ruta-evento.json>", file=sys.stderr)
        return 2

    load_dotenv()
    ruta_evento = sys.argv[1]

    try:
        evento = json.loads(Path(ruta_evento).read_text(encoding="utf-8"))
        jsonschema.validate(evento, ESQUEMA_EVENTO)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        print(f"evento invalido, no se puede procesar {ruta_evento}: {exc}", file=sys.stderr)
        return 1

    campana = cargar_campana("config/campana.yaml")
    store = KontaktuStore("estado/kontaktu.sqlite3")
    try:
        store.begin()
        grafo = construir_grafo(campana, store)
        grafo.invoke({"evento": evento})
        store.commit()
        return 0
    except Exception as exc:  # R8: un fallo aqui no debe tumbar el proceso sin control
        store.rollback()
        print(f"fallo procesando {ruta_evento}: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
