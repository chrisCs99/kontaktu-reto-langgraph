"""Calculo de fechas: zona horaria Europe/Madrid, ventana de llamadas y dias habiles.

Convencion de reintento dentro de un rango [min, max]: se apunta al punto medio del
rango y luego se encaja en la siguiente franja valida de la ventana de llamadas. Se
fijo mirando ejemplo-resuelto/ (evento a las 10:31, ocupado_minutos_min=30/max=90,
llamada programada a las 11:31 => +60min, el punto medio de [30,90]).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kontaktu.config import Campana

MAX_DIAS_BUSQUEDA_VENTANA = 8  # cota de seguridad para no iterar sin fin si la config estuviera mal


def parse_dt(valor: str) -> datetime:
    return datetime.fromisoformat(valor)


def parse_dt_localizado(valor: str, campana: Campana) -> datetime:
    """Como parse_dt, pero si el valor viene sin offset (p.ej. el LLM se lo
    salta) lo trata como hora local de la campaña en vez de hora local del
    sistema que ejecuta el proceso."""
    dt = datetime.fromisoformat(valor)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(campana.zona_horaria))
    return dt


def a_zona_campana(dt: datetime, campana: Campana) -> datetime:
    return dt.astimezone(ZoneInfo(campana.zona_horaria))


def encajar_en_ventana(dt: datetime, campana: Campana) -> datetime:
    """Empuja dt al primer instante >= dt que cae dentro de la ventana de llamadas."""
    candidato = a_zona_campana(dt, campana)
    for _ in range(MAX_DIAS_BUSQUEDA_VENTANA):
        franja = campana.ventana_del_dia(candidato.weekday())
        if franja is not None:
            inicio = time.fromisoformat(franja[0])
            fin = time.fromisoformat(franja[1])
            if inicio <= candidato.time() <= fin:
                return candidato
            if candidato.time() < inicio:
                return candidato.replace(hour=inicio.hour, minute=inicio.minute, second=0, microsecond=0)
        candidato = (candidato + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    raise RuntimeError("no se encontro una franja valida en la ventana de llamadas")


def proximo_intento_general(occurred_at: datetime, campana: Campana) -> datetime:
    objetivo = occurred_at + timedelta(hours=campana.separacion_minima_horas)
    return encajar_en_ventana(objetivo, campana)


def proximo_intento_ocupado(occurred_at: datetime, campana: Campana) -> datetime:
    delay_min = (campana.ocupado_minutos_min + campana.ocupado_minutos_max) / 2
    objetivo = occurred_at + timedelta(minutes=delay_min)
    return encajar_en_ventana(objetivo, campana)


def proximo_intento_cortada(occurred_at: datetime, campana: Campana) -> datetime:
    max_min = campana.cortada_horas_max * 60
    delay_min = (campana.cortada_minutos_min + max_min) / 2
    objetivo = occurred_at + timedelta(minutes=delay_min)
    return encajar_en_ventana(objetivo, campana)


def encajar_callback(hora_pedida: datetime, campana: Campana) -> tuple[datetime, bool]:
    """Devuelve (hora_final, se_movio). se_movio=True dispara aviso_cambio_hora (caso 12)."""
    final = encajar_en_ventana(hora_pedida, campana)
    return final, final != a_zona_campana(hora_pedida, campana)


def sumar_horas_naturales(base: datetime, horas: int) -> datetime:
    return base + timedelta(hours=horas)


def sumar_dias_naturales(base: datetime, dias: int) -> datetime:
    return base + timedelta(days=dias)


def sumar_dias_habiles(base: datetime, dias: int, campana: Campana) -> datetime:
    cursor = base
    restantes = dias
    while restantes > 0:
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() in campana.dias_habiles:
            restantes -= 1
    return cursor
