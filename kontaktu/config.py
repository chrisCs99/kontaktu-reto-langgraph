"""Carga tipada de config/campana.yaml. No se modifica ese fichero (restriccion del enunciado)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


@dataclass(frozen=True)
class Campana:
    system_key: str
    organization_id: str
    zona_horaria: str
    ventana_llamadas: dict[int, Optional[tuple[str, str]]]  # weekday() 0=lunes .. 6=domingo
    max_intentos: int
    separacion_minima_horas: int
    ocupado_minutos_min: int
    ocupado_minutos_max: int
    cortada_minutos_min: int
    cortada_horas_max: int
    canal_respaldo: str
    dias_habiles: set[int]
    documentacion_lead_horas: int
    seguimiento_comercial_dias_habiles: int
    confirmar_visita_margen_horas: int
    vencimiento_por_defecto_dias: int

    def ventana_del_dia(self, weekday: int) -> Optional[tuple[str, str]]:
        return self.ventana_llamadas.get(weekday)


def cargar_campana(path: str | Path = "config/campana.yaml") -> Campana:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    campana = data["campana"]
    ventana_raw = data["ventana_llamadas"]
    ventana: dict[int, Optional[tuple[str, str]]] = {}
    for idx, nombre in enumerate(_DIAS):
        franja = ventana_raw.get(nombre) or []
        ventana[idx] = (franja[0], franja[1]) if len(franja) == 2 else None
    reintentos = data["reintentos"]
    recordatorios = data["recordatorios"]
    tareas = data["tareas"]
    dias_habiles = {_DIAS.index(d) for d in data["dias_habiles"]}
    return Campana(
        system_key=campana["system_key"],
        organization_id=campana["organization_id"],
        zona_horaria=campana["zona_horaria"],
        ventana_llamadas=ventana,
        max_intentos=reintentos["max_intentos"],
        separacion_minima_horas=reintentos["separacion_minima_horas"],
        ocupado_minutos_min=reintentos["ocupado_minutos_min"],
        ocupado_minutos_max=reintentos["ocupado_minutos_max"],
        cortada_minutos_min=reintentos["cortada_minutos_min"],
        cortada_horas_max=reintentos["cortada_horas_max"],
        canal_respaldo=data["canal_respaldo"],
        dias_habiles=dias_habiles,
        documentacion_lead_horas=recordatorios["documentacion_lead_horas"],
        seguimiento_comercial_dias_habiles=recordatorios["seguimiento_comercial_dias_habiles"],
        confirmar_visita_margen_horas=tareas["confirmar_visita_margen_horas"],
        vencimiento_por_defecto_dias=tareas["vencimiento_por_defecto_dias"],
    )
