"""El grafo LangGraph del orquestador post-llamada.

Diseño (ver README para la justificacion completa):

    START -> guard --bloqueado--> finalizar_bloqueado -> persistir -> END
                  \\--call.ended--> contar_intento -> clasificar_determinista
                  |                     --resuelto--> aplicar_reglas_negocio -> persistir -> END
                  |                     --ambiguo---> clasificar_llm -> aplicar_reglas_negocio -> persistir -> END
                  \\--message.received--> procesar_mensaje -> persistir -> END

`guard` filtra en un solo sitio los dos casos que no son un caso de negocio (R6:
otra organizacion: R5: reentrega). `clasificar_determinista` resuelve por
señalizacion lo que casos.md dice que se resuelve por señalizacion (ahorra
llamadas al LLM, como pide ejemplo-resuelto/README.md). Todo lo que necesita leer
la transcripcion cae en `clasificar_llm`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from kontaktu import crm, timewin
from kontaktu.config import Campana
from kontaktu.llm import clasificar_con_llm
from kontaktu.models import ESTADO_POR_ETIQUETA
from kontaktu.store import KontaktuStore

SIP_SIN_RESPUESTA = {408, 480}
SIP_OCUPADO = 486
SIP_RECHAZADA = 603
AMD_BUZON = {"machine-vm", "machine-unavailable"}


class EstadoGrafo(TypedDict, total=False):
    evento: dict
    bloqueado: bool
    motivo_bloqueo: Optional[Literal["otra_organizacion", "reentrega"]]
    decision_previa: Optional[dict]
    lead: dict
    intentos_voz: int
    etiqueta: str
    motivo: str
    confianza: float
    nota_contexto: str
    callback_iso: Optional[str]
    resuelto_por_reglas: bool
    ordenes: list[dict]


# --------------------------------------------------------------------------
# Helpers de construccion de ordenes (mapean 1:1 con esquemas/crm-openapi.yaml)
# --------------------------------------------------------------------------

def _orden_programar_llamada(evento: dict, cuando: datetime, motivo: str, nota_contexto: str = "") -> dict:
    return crm.construir_orden(evento["event_id"], "programar_llamada", evento["idempotency_key"], {
        "entry_id": evento["campaign"]["entry_id"],
        "telefono": evento["lead"]["phone"],
        "no_antes_de": cuando.isoformat(),
        "motivo": motivo,
        "nota_contexto": nota_contexto,
    })


def _orden_whatsapp(evento: dict, plantilla: str, parametros: Optional[dict] = None) -> dict:
    return crm.construir_orden(evento["event_id"], "enviar_plantilla_whatsapp", evento["idempotency_key"], {
        "organization_id": evento["organization_id"],
        "telefono": evento["lead"]["phone"],
        "plantilla": plantilla,
        "parametros": parametros or {},
        "idioma": evento["lead"].get("language", "es"),
    }, distintivo=plantilla)


def _orden_tarea(evento: dict, tipo: str, titulo: str, vence_el: datetime, detalle: str = "") -> dict:
    return crm.construir_orden(evento["event_id"], "crear_tarea", evento["idempotency_key"], {
        "contact_id": evento["lead"]["contact_id"],
        "call_id": (evento.get("telephony") or {}).get("call_id"),
        "tipo": tipo,
        "titulo": titulo,
        "detalle": detalle,
        "vence_el": vence_el.isoformat(),
        "asignada_a": "comercial_asignado",
    }, distintivo=tipo)


def _orden_no_contactar(evento: dict, motivo: str) -> dict:
    return crm.construir_orden(evento["event_id"], "marcar_no_contactar", evento["idempotency_key"], {
        "telefono": evento["lead"]["phone"],
        "contact_id": evento["lead"]["contact_id"],
        "canal": "todos",
        "motivo": motivo,
        "origen": "call.ended",
    })


def _orden_cerrar_llamada(evento: dict, etiqueta: str, motivo: str, confianza: float) -> dict:
    return crm.construir_orden(evento["event_id"], "cerrar_llamada", evento["idempotency_key"], {
        "entry_id": evento["campaign"]["entry_id"],
        "status": ESTADO_POR_ETIQUETA[etiqueta],
        "etiqueta": etiqueta,
        "motivo": motivo,
        "confianza": confianza,
        "duration_seconds": (evento.get("telephony") or {}).get("duration_seconds", 0),
    })


def _orden_cancelar_recordatorio(evento: dict, reminder_id: str, motivo: str) -> dict:
    return crm.construir_orden(evento["event_id"], "cancelar_recordatorio", evento["idempotency_key"], {
        "reminder_id": reminder_id,
        "motivo": motivo,
    }, distintivo=reminder_id)


# --------------------------------------------------------------------------
# Fabrica del grafo: los nodos capturan campana/store como dependencias
# --------------------------------------------------------------------------

def construir_grafo(campana: Campana, store: KontaktuStore):

    # -- guard: R6 (otra organizacion) + R5 (reentrega) ---------------------
    def nodo_guard(estado: EstadoGrafo) -> dict:
        evento = estado["evento"]
        previa = store.decision_previa(evento["idempotency_key"])
        if previa is not None:
            return {"bloqueado": True, "motivo_bloqueo": "reentrega", "decision_previa": previa}
        if evento["organization_id"] != campana.organization_id:
            return {"bloqueado": True, "motivo_bloqueo": "otra_organizacion", "decision_previa": None}
        return {"bloqueado": False}

    def decidir_tras_guard(estado: EstadoGrafo) -> str:
        if estado.get("bloqueado"):
            return "bloqueado"
        return "call_ended" if estado["evento"]["type"] == "call.ended" else "mensaje"

    # -- contar intento (R4) --------------------------------------------------
    def nodo_contar_intento(estado: EstadoGrafo) -> dict:
        contact_id = estado["evento"]["lead"]["contact_id"]
        intentos = store.lead_incrementar_intento(contact_id)
        lead = store.lead_obtener(contact_id)
        return {"intentos_voz": intentos, "lead": lead}

    # -- clasificacion determinista (ahorra LLM cuando la señal ya lo dice) --
    def nodo_clasificar_determinista(estado: EstadoGrafo) -> dict:
        evento = estado["evento"]
        t = evento.get("telephony") or {}
        ao = evento.get("agent_outcome") or {}
        amd = t.get("amd") or {}
        sip = t.get("sip_status_code")

        sip_texto = t.get("sip_status", "")

        if ao.get("appointment"):
            return _resuelto("visita_reservada", "el agente creó la cita durante la llamada", 0.99)
        if ao.get("call_outcome") == "dnc":
            return _resuelto("no_contactar", "agent_outcome marca la llamada como dnc", 0.97)
        if sip in SIP_SIN_RESPUESTA:
            return _resuelto("sin_respuesta", f"{sip} {sip_texto}: no contesta".strip(), 0.97)
        if sip == SIP_OCUPADO:
            return _resuelto("ocupado", f"{sip} {sip_texto}: la línea comunica".strip(), 0.97)
        if sip == SIP_RECHAZADA:
            return _resuelto("rechazada", f"{sip} {sip_texto}: rechazo activo antes de descolgar".strip(), 0.97)
        if amd.get("result") in AMD_BUZON:
            return _resuelto("buzon", f"amd.result={amd.get('result')}, buzón de voz", 0.9)
        if amd.get("result") == "machine-ivr":
            return _resuelto("otro", "amd.result=machine-ivr, fuera del catálogo cerrado", 0.6)
        if sip is not None and 500 <= sip < 600:
            return _resuelto("otro", f"fallo de trunk antes de conectar (sip {sip})", 0.6)
        return {"resuelto_por_reglas": False}

    def _resuelto(etiqueta: str, motivo: str, confianza: float) -> dict:
        return {
            "resuelto_por_reglas": True,
            "etiqueta": etiqueta,
            "motivo": motivo,
            "confianza": confianza,
            "nota_contexto": "",
            "callback_iso": None,
        }

    def decidir_tras_determinista(estado: EstadoGrafo) -> str:
        return "resuelto" if estado.get("resuelto_por_reglas") else "ambiguo"

    # -- clasificacion LLM: solo para lo que depende de leer la transcripcion
    def nodo_clasificar_llm(estado: EstadoGrafo) -> dict:
        resultado = clasificar_con_llm(estado["evento"])
        return {
            "etiqueta": resultado.etiqueta,
            "motivo": resultado.motivo,
            "confianza": resultado.confianza,
            "nota_contexto": resultado.nota_contexto,
            "callback_iso": resultado.callback_iso,
        }

    # -- reglas de negocio: etiqueta -> ordenes (N1-N5, R3, R4) --------------
    def nodo_aplicar_reglas_negocio(estado: EstadoGrafo) -> dict:
        evento = estado["evento"]
        etiqueta = estado["etiqueta"]
        motivo = estado["motivo"]
        confianza = estado["confianza"]
        lead = estado["lead"]
        contact_id = evento["lead"]["contact_id"]
        occurred_at = timewin.parse_dt(evento["occurred_at"])
        ordenes: list[dict] = []

        if lead["no_contactar"]:
            # N2: un lead marcado DNC no recibe ninguna orden saliente, ni aunque
            # esta nueva llamada trajera otra etiqueta.
            ordenes.append(_orden_cerrar_llamada(evento, etiqueta, motivo, confianza))
            return {"ordenes": ordenes}

        if etiqueta == "visita_reservada":
            appt = evento["agent_outcome"]["appointment"]
            inicio = timewin.parse_dt(appt["start_time"])
            vence = inicio - timedelta(hours=campana.confirmar_visita_margen_horas)
            nombre = evento["lead"].get("full_name") or contact_id
            ordenes.append(_orden_tarea(
                evento, "confirmar_visita_direccion",
                f"Confirmar dirección de visita - {nombre}", vence,
                detalle=f"Visita reservada para {appt['start_time']}.",
            ))

        elif etiqueta == "documentacion_enviada":
            vence_lead = timewin.sumar_horas_naturales(occurred_at, campana.documentacion_lead_horas)
            orden_lead = crm.construir_orden(evento["event_id"], "programar_recordatorio", evento["idempotency_key"], {
                "contact_id": contact_id, "canal": "whatsapp_lead", "plantilla": "recordatorio_documentacion",
                "cuando": vence_lead.isoformat(), "cancelar_si": "lead_responde",
            }, distintivo="lead")
            store.recordatorio_crear(crm.reminder_id(orden_lead["idempotency_key"]), contact_id, "whatsapp_lead")
            ordenes.append(orden_lead)

            vence_comercial = timewin.sumar_dias_habiles(occurred_at, campana.seguimiento_comercial_dias_habiles, campana)
            orden_comercial = crm.construir_orden(evento["event_id"], "programar_recordatorio", evento["idempotency_key"], {
                "contact_id": contact_id, "canal": "tarea_comercial", "tipo_tarea": "llamar_a_mano",
                "cuando": vence_comercial.isoformat(), "cancelar_si": "lead_responde",
            }, distintivo="comercial")
            store.recordatorio_crear(crm.reminder_id(orden_comercial["idempotency_key"]), contact_id, "tarea_comercial")
            ordenes.append(orden_comercial)

        elif etiqueta == "documentacion_pendiente":
            store.lead_marcar_whatsapp_rechazado(contact_id)  # N1 a futuro
            vence = timewin.sumar_dias_naturales(occurred_at, campana.vencimiento_por_defecto_dias)
            ordenes.append(_orden_tarea(
                evento, "enviar_documentacion_email", "Enviar documentación por email (rechaza WhatsApp)",
                vence, detalle=estado.get("nota_contexto", ""),
            ))

        elif etiqueta == "callback":
            if estado.get("callback_iso"):
                pedida = timewin.parse_dt_localizado(estado["callback_iso"], campana)
            else:
                pedida = occurred_at + timedelta(hours=campana.separacion_minima_horas)
            final, se_movio = timewin.encajar_callback(pedida, campana)
            ordenes.append(_orden_programar_llamada(
                evento, final, "callback solicitado por el lead", estado.get("nota_contexto", ""),
            ))
            if se_movio:
                ordenes.append(_orden_whatsapp(evento, "aviso_cambio_hora", {
                    "hora_propuesta": final.isoformat(),
                }))

        elif etiqueta in ("sin_respuesta", "ocupado", "buzon"):
            nota = "no se llegó a hablar con el lead" if not evento.get("transcript") else estado.get("nota_contexto", "")
            if lead["intentos_voz"] >= campana.max_intentos:
                store.lead_marcar_canal_respaldo_usado(contact_id)
                ordenes.append(_orden_whatsapp(evento, "primer_toque_respaldo"))
            elif etiqueta == "ocupado":
                cuando = timewin.proximo_intento_ocupado(occurred_at, campana)
                ordenes.append(_orden_programar_llamada(evento, cuando, "línea comunicando, reintento corto", nota))
            else:
                cuando = timewin.proximo_intento_general(occurred_at, campana)
                motivo_reintento = "no contesta, reintento" if etiqueta == "sin_respuesta" else "buzón de voz, reintento"
                ordenes.append(_orden_programar_llamada(evento, cuando, motivo_reintento, nota))

        elif etiqueta in ("cortada", "visita_sin_confirmar"):
            cuando = timewin.proximo_intento_cortada(occurred_at, campana)
            motivo_reintento = (
                "visita acordada de palabra, se llama para confirmarla en el sistema"
                if etiqueta == "visita_sin_confirmar" else "llamada cortada a media conversacion, se recupera"
            )
            ordenes.append(_orden_programar_llamada(evento, cuando, motivo_reintento, estado.get("nota_contexto", "")))
            # N4: segunda cortada/visita_sin_confirmar del mismo lead -> revisar_llamada ademas
            previas = store.historial_contar_etiquetas(contact_id, ("cortada", "visita_sin_confirmar"))
            if previas >= 1:
                vence = timewin.sumar_dias_naturales(occurred_at, campana.vencimiento_por_defecto_dias)
                ordenes.append(_orden_tarea(
                    evento, "revisar_llamada", "Revisar: segunda llamada cortada con este lead",
                    vence, detalle=motivo,
                ))

        elif etiqueta == "persona_equivocada":
            vence = timewin.sumar_dias_naturales(occurred_at, campana.vencimiento_por_defecto_dias)
            ordenes.append(_orden_tarea(evento, "verificar_telefono", "Verificar teléfono del lead", vence, detalle=motivo))

        elif etiqueta == "no_contactar":
            store.lead_marcar_no_contactar(contact_id)
            ordenes.append(_orden_no_contactar(evento, motivo))

        elif etiqueta == "rechazada":
            store.lead_marcar_canal_respaldo_usado(contact_id)
            ordenes.append(_orden_whatsapp(evento, "primer_toque_respaldo"))

        elif etiqueta == "descartado":
            pass  # ninguna orden mas alla de cerrar_llamada

        elif etiqueta == "otro":
            vence = timewin.sumar_dias_naturales(occurred_at, campana.vencimiento_por_defecto_dias)
            ordenes.append(_orden_tarea(evento, "revisar_llamada", "Revisar llamada sin clasificar", vence, detalle=motivo))

        ordenes.append(_orden_cerrar_llamada(evento, etiqueta, motivo, confianza))
        return {"ordenes": ordenes}

    # -- rama message.received: R7 -------------------------------------------
    def nodo_procesar_mensaje(estado: EstadoGrafo) -> dict:
        evento = estado["evento"]
        contact_id = evento["lead"]["contact_id"]
        activos = store.recordatorios_activos(contact_id)
        ordenes = []
        for rid in activos:
            ordenes.append(_orden_cancelar_recordatorio(evento, rid, "el lead respondio, se cancela el seguimiento"))
            store.recordatorio_cancelar(rid)
        return {
            "etiqueta": "no_aplica", "motivo": "el lead respondio por WhatsApp",
            "confianza": 1.0, "nota_contexto": "", "ordenes": ordenes,
        }

    # -- persistencia: jsonl + sqlite (transaccion ya abierta por run.py) ---
    def nodo_persistir(estado: EstadoGrafo) -> dict:
        evento = estado["evento"]
        if estado.get("bloqueado"):
            if estado["motivo_bloqueo"] == "reentrega":
                previa = estado["decision_previa"]
                etiqueta, motivo, confianza = previa["etiqueta"], previa["motivo"], previa["confianza"]
            else:
                etiqueta, motivo, confianza = "no_aplica", f"evento de otra organización ({evento['organization_id']})", 1.0
            ordenes: list[dict] = []
        else:
            etiqueta, motivo, confianza = estado["etiqueta"], estado["motivo"], estado["confianza"]
            ordenes = estado.get("ordenes", [])

        _escribir_jsonl("salida/ordenes.jsonl", ordenes)
        call_id = (evento.get("telephony") or {}).get("call_id")
        linea_decision = {
            "event_id": evento["event_id"], "call_id": call_id, "etiqueta": etiqueta,
            "motivo": motivo, "confianza": confianza, "ordenes": [o["orden_id"] for o in ordenes],
        }
        _escribir_jsonl("salida/decisiones.jsonl", [linea_decision])

        if not (estado.get("bloqueado") and estado["motivo_bloqueo"] == "reentrega"):
            store.guardar_decision(
                evento["idempotency_key"], evento["event_id"], call_id,
                etiqueta, motivo, confianza, [o["orden_id"] for o in ordenes],
            )
        if evento["type"] == "call.ended" and not estado.get("bloqueado"):
            store.historial_agregar(evento["lead"]["contact_id"], evento["event_id"], etiqueta, evento["occurred_at"])
        return {}

    grafo = StateGraph(EstadoGrafo)
    grafo.add_node("guard", nodo_guard)
    grafo.add_node("contar_intento", nodo_contar_intento)
    grafo.add_node("clasificar_determinista", nodo_clasificar_determinista)
    grafo.add_node("clasificar_llm", nodo_clasificar_llm)
    grafo.add_node("aplicar_reglas_negocio", nodo_aplicar_reglas_negocio)
    grafo.add_node("procesar_mensaje", nodo_procesar_mensaje)
    grafo.add_node("persistir", nodo_persistir)

    grafo.add_edge(START, "guard")
    grafo.add_conditional_edges("guard", decidir_tras_guard, {
        "bloqueado": "persistir",
        "call_ended": "contar_intento",
        "mensaje": "procesar_mensaje",
    })
    grafo.add_edge("contar_intento", "clasificar_determinista")
    grafo.add_conditional_edges("clasificar_determinista", decidir_tras_determinista, {
        "resuelto": "aplicar_reglas_negocio",
        "ambiguo": "clasificar_llm",
    })
    grafo.add_edge("clasificar_llm", "aplicar_reglas_negocio")
    grafo.add_edge("aplicar_reglas_negocio", "persistir")
    grafo.add_edge("procesar_mensaje", "persistir")
    grafo.add_edge("persistir", END)

    return grafo.compile()


def _escribir_jsonl(path: str, lineas: list[dict]) -> None:
    if not lineas:
        return
    import json
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for linea in lineas:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
