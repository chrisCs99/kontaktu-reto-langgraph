"""Nodo LLM: solo se invoca cuando la señalizacion no resuelve la etiqueta por si sola.

Ver prompts/clasificar_llamada.md para el prompt versionado y la justificacion del
modelo elegido.
"""
from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI

from kontaktu.models import ClasificacionLLM

_SYSTEM_PROMPT_CACHE: str | None = None


def _cargar_system_prompt() -> str:
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is not None:
        return _SYSTEM_PROMPT_CACHE
    texto = Path("prompts/clasificar_llamada.md").read_text(encoding="utf-8")
    inicio = texto.index("```", texto.index("## System prompt")) + 3
    fin = texto.index("```", inicio)
    _SYSTEM_PROMPT_CACHE = texto[inicio:fin].strip()
    return _SYSTEM_PROMPT_CACHE


def _mensaje_usuario(evento: dict) -> str:
    telephony = evento.get("telephony", {}) or {}
    agent_outcome = evento.get("agent_outcome", {}) or {}
    transcript = evento.get("transcript", []) or []
    lineas = [f"[{m['role']}] {m['message']}" for m in transcript]
    return (
        f"occurred_at: {evento['occurred_at']}\n\n"
        f"Señalización de telefonía:\n"
        f"  sip_status_code: {telephony.get('sip_status_code')}\n"
        f"  disconnect_reason: {telephony.get('disconnect_reason')}\n"
        f"  hung_up_by: {telephony.get('hung_up_by')}\n"
        f"  duration_seconds: {telephony.get('duration_seconds')}\n"
        f"  amd: {telephony.get('amd')}\n\n"
        f"agent_outcome:\n"
        f"  call_outcome: {agent_outcome.get('call_outcome')!r}\n"
        f"  reason: {agent_outcome.get('reason')!r}\n"
        f"  slots_snapshot: {agent_outcome.get('slots_snapshot')}\n\n"
        f"Transcripción:\n" + ("\n".join(lineas) if lineas else "(vacía)")
    )


def clasificar_con_llm(evento: dict) -> ClasificacionLLM:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    modelo = os.environ.get("MODELO", "gpt-4o-mini")
    completion = client.beta.chat.completions.parse(
        model=modelo,
        messages=[
            {"role": "system", "content": _cargar_system_prompt()},
            {"role": "user", "content": _mensaje_usuario(evento)},
        ],
        response_format=ClasificacionLLM,
        temperature=0,
    )
    resultado = completion.choices[0].message.parsed
    if resultado is None:
        raise RuntimeError("el LLM no devolvio una clasificacion valida (refusal o parseo fallido)")
    return resultado
