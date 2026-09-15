# Orquestador post-llamada · Kontaktu

## Cómo se ejecuta

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; en Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # y pega tu OPENAI_API_KEY

python run.py eventos/01-call-ended-nuria.json
```

Un evento por invocación, tal como pide el contrato. Para reproducir el lote de
ejemplo completo, en el orden de `eventos/orden.txt`:

```bash
for f in $(grep -v '^#' eventos/orden.txt); do python run.py "eventos/$f"; done
```

`salida/decisiones.jsonl` y `salida/ordenes.jsonl` se crean por *append*; para una
corrida limpia, borra `salida/` y `estado/` antes (ambas están en `.gitignore`
porque son generadas, no código — el enunciado evalúa "desde cero").
`estado/kontaktu.sqlite3` es el único estado que sobrevive entre procesos
(intentos por lead, recordatorios activos, historial de llamadas, bajas).

## Arquitectura

Un grafo LangGraph por invocación (`kontaktu/graph.py`):

```
START -> guard --bloqueado (R6/R5)--------------------> persistir -> END
              \--call.ended--> contar_intento (R4) -> clasificar_determinista
              |                    --resuelto--------> aplicar_reglas_negocio -> persistir -> END
              |                    --ambiguo---> clasificar_llm -----^
              \--message.received--> procesar_mensaje (R7) ---------> persistir -> END
```

- **`guard`** concentra en un solo sitio los dos casos que no son un caso de
  negocio: otra `organization_id` (R6) y reentrega por `idempotency_key` (R5),
  ambos vía una tabla SQLite de decisiones ya emitidas.
- **`clasificar_determinista`** resuelve por señalización pura (SIP, `amd`,
  `agent_outcome.appointment`/`call_outcome`) los 8 casos que `casos.md` y el
  propio `ejemplo-resuelto/` dicen que no necesitan LLM: `sin_respuesta`,
  `ocupado`, `rechazada`, `buzon` (ambas variantes), `visita_reservada`,
  `no_contactar` (vía `call_outcome=="dnc"`), y el catch-all `otro` para 5xx /
  `amd.result=machine-ivr`. Validado byte a byte contra `ejemplo-resuelto/`.
- **`clasificar_llm`** solo se invoca para lo que de verdad depende de leer la
  transcripción: `persona_equivocada`, `callback` (incluye parsear la hora en
  texto libre), `cortada` vs `visita_sin_confirmar`, `documentacion_enviada` vs
  `documentacion_pendiente`, `descartado` y `otro` por contenido. Recibe
  transcripción *y* señalización juntas (R1). Salida estructurada con Pydantic
  (`kontaktu/models.py::ClasificacionLLM`) vía
  `client.beta.chat.completions.parse`, así el catálogo cerrado se garantiza
  por esquema, no por parseo de texto. Prompt versionado en
  `prompts/clasificar_llamada.md`.
- **`aplicar_reglas_negocio`** traduce etiqueta + estado persistido (intentos,
  flags de lead) en las órdenes CRM, aplicando N1-N5 y las ventanas/plazos de
  `campana.yaml` (`kontaktu/timewin.py`).
- **Modelo elegido: `gpt-4o-mini`** (configurable por `MODELO` en `.env`). La
  tarea es clasificación cerrada + extracción de una fecha relativa sobre
  transcripciones cortas en español; no hace falta un modelo de frontera, y
  `gpt-4o-mini` da *structured outputs* fiables a bajo coste/latencia.

## Qué decidí dejar fuera y por qué

- **No confío en las claves libres de `agent_outcome.slots_snapshot`**
  (`docs_enviadas`, `canal_consentido`, `visita_acordada_verbal`...) como señal
  determinista, aunque aparecen así en varios ejemplos y hubieran resuelto
  `documentacion_enviada`/`documentacion_pendiente`/`visita_sin_confirmar` sin
  LLM. El esquema las declara `additionalProperties: true` y `casos.md` no las
  documenta como contrato — apostar por esos nombres de clave exactos habría
  sido sobreajustar al lote de ejemplo. Las paso al LLM como contexto, no como
  verdad estructural.
- **El canal de respaldo (N3) no comprueba el flag `whatsapp_rechazado`
  (N1)** antes de enviarse: si un lead agota los 3 intentos de voz *después*
  de haber rechazado WhatsApp en una llamada anterior, igual recibe
  `primer_toque_respaldo` por ese canal. El catálogo no define un tercer canal
  alternativo, así que preferí no inventarme uno; lo documento aquí en vez de
  silenciarlo.
- **`confianza` en la rama determinista son constantes fijas** (0.9-0.99), no
  probabilidades calibradas — la señalización no es estadística, es una regla
  cierta o no lo es.
- Sin tests automatizados (el enunciado no los pide); ver más abajo cómo
  verifiqué el comportamiento.

## Cómo lo verifiqué

1. **Contra el ground truth dado**: `eventos/02-call-ended-tomas.json` (el
   mismo evento de `ejemplo-resuelto/`) reproduce exactamente
   `ejemplo-resuelto/salida/ordenes.jsonl` — mismo `orden_id` (mismo hash de
   8 hex sobre la misma `idempotency_key`), mismo `motivo`, mismo
   `nota_contexto`, mismo `no_antes_de`.
2. **Corrida completa de los 16 eventos** en el orden de `orden.txt`, revisada
   línea a línea en `visor/index.html` y contrastada a mano con `casos.md`.
3. **Casos cruzados construidos a propósito**:
   - Nuria (`c_301`) aparece en los eventos 01, 05 y 12: intentos 1 y 2
     programan reintento por voz; el intento 3 (`buzon`) agota
     `max_intentos=3` y dispara correctamente el canal de respaldo (N3) en vez
     de un cuarto intento.
   - Evento 16 (`organization_id` distinta): cero órdenes, ni `cerrar_llamada`.
     Reprocesado dos veces: la segunda vez lo trata el `guard` como reentrega
     (misma `idempotency_key`) y no duplica nada.
   - R8: forcé fallos (sin `OPENAI_API_KEY`) en los eventos que necesitan LLM
     — cada uno sale con código 1, sin tocar `salida/*.jsonl` de forma parcial
     (la transacción SQLite del evento se revierte) y sin afectar a los demás
     eventos de la tanda.
