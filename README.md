# Orquestador post-llamada · Kontaktu

## Cómo se ejecuta

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # pega tu OPENAI_API_KEY

python run.py eventos/01-call-ended-nuria.json
# lote completo, en el orden de orden.txt:
for f in $(grep -v '^#' eventos/orden.txt); do python run.py "eventos/$f"; done
```

`salida/decisiones.jsonl` y `salida/ordenes.jsonl` se crean por *append*; borra
`salida/` y `estado/` para una corrida limpia (van en `.gitignore`, son
generadas). `estado/kontaktu.sqlite3` es el único estado que sobrevive entre
procesos. `kontaktu/llm.py` admite un `OPENAI_BASE_URL` opcional en `.env`
(sin definir, usa la API real de OpenAI) para probar contra cualquier
endpoint compatible sin gastar la clave — así validé la rama LLM antes de
tenerla (ver "Cómo lo verifiqué").

## Arquitectura

Un grafo LangGraph por invocación (`kontaktu/graph.py`):

```
START -> guard --bloqueado (R6/R5)--------------------> persistir -> END
              \--call.ended--> contar_intento (R4) -> clasificar_determinista
              |                    --resuelto--------> aplicar_reglas_negocio -> persistir -> END
              |                    --ambiguo---> clasificar_llm -----^
              \--message.received--> procesar_mensaje (R7) ---------> persistir -> END
```

- **`guard`** concentra R6 (otra `organization_id`) y R5 (reentrega por
  `idempotency_key`) en un solo sitio, contra una tabla SQLite de decisiones
  ya emitidas.
- **`clasificar_determinista`** resuelve por señalización pura (SIP, `amd`,
  `agent_outcome.appointment`/`call_outcome`) los 8 casos que `casos.md` y
  `ejemplo-resuelto/` dicen que no necesitan LLM. Validado byte a byte contra
  ese ejemplo.
- **`clasificar_llm`** solo entra cuando de verdad hace falta leer la
  transcripción (`callback`, `cortada` vs `visita_sin_confirmar`,
  `documentacion_enviada` vs `pendiente`, `persona_equivocada`, `descartado`,
  `otro`). Salida estructurada con Pydantic
  (`client.beta.chat.completions.parse`), así el catálogo cerrado lo
  garantiza el esquema, no el parseo. Prompt en `prompts/clasificar_llamada.md`.
- **`aplicar_reglas_negocio`** traduce etiqueta + estado persistido (intentos,
  flags de lead) en órdenes CRM, aplicando N1-N5 y `campana.yaml`
  (`kontaktu/timewin.py`).
- **Modelo: `gpt-4o-mini`** — clasificación cerrada + extracción de una fecha
  relativa sobre transcripciones cortas; no hace falta un modelo de frontera.

## Qué decidí dejar fuera y por qué

- **No confío en las claves libres de `agent_outcome.slots_snapshot`**
  (`docs_enviadas`, `canal_consentido`...) como señal determinista, aunque
  habrían resuelto algún caso sin LLM: el esquema las declara
  `additionalProperties: true`, no son contrato. Las paso al LLM como
  contexto, no como verdad estructural.
- **El canal de respaldo (N3) no comprueba `whatsapp_rechazado` (N1)**: si un
  lead agota los intentos de voz tras rechazar WhatsApp, igual recibe
  `primer_toque_respaldo` por ahí. El catálogo no define un tercer canal.
- **Sin reintentos propios ante fallos de OpenAI**: el SDK ya reintenta los
  errores transitorios por defecto, y un fallo revierte toda la transacción
  SQLite del evento — relanzar el mismo evento más tarde ya es seguro e
  idempotente sin lógica extra.
- Sin tests automatizados (no se piden); ver verificación abajo.

## Cómo lo verifiqué

1. **Contra el ground truth**: `eventos/02-call-ended-tomas.json` reproduce
   `ejemplo-resuelto/salida/ordenes.jsonl` exactamente (mismo `orden_id`,
   `motivo`, `nota_contexto`, `no_antes_de`).
2. **Los 16 eventos con un LLM real**, antes de tener la clave de OpenAI:
   `run.py` real apuntado por `OPENAI_BASE_URL` a la capa de compatibilidad
   de Gemini. 16/16 bien clasificados — el `callback` del evento 09 calcula
   la misma fecha que usa el propio enunciado como ejemplo (sección 4.2), R7
   cierra el ciclo completo (evento 08 crea 2 recordatorios, evento 14 los
   cancela), y un `RateLimitError` real de la capa gratuita en dos eventos
   confirmó R8 sin querer (no bloqueó al resto).
3. **Los 3 casos sin evento de ejemplo** (`rechazada`, `callback` fuera de
   ventana, `descartado`, marcados ⚠ en `casos.md`):
   `python -m verificacion.casos_sin_ejemplo` los construye a mano y confirma
   que hacen justo lo que describe el catálogo.
4. **Casos cruzados**: Nuria (eventos 01/05/12) agota `max_intentos=3` y
   dispara el canal de respaldo (N3) en el tercer intento; evento 16 (otra
   organización) reprocesado dos veces no duplica nada (reentrega); una
   segunda `cortada` forzada a mano para el mismo lead dispara la tarea
   `revisar_llamada` de N4.
