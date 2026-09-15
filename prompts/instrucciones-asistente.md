# Instrucciones dadas al asistente de programación (Claude Code)

Opcional según el enunciado ("si además quieres incluir los que le diste a tu
asistente de programación, mejor"). Resumen de las instrucciones de fondo que
guiaron el desarrollo, no la transcripción literal de la sesión.

## Encargo inicial

> Nuestros agentes de voz llaman a leads inmobiliarios, y cada llamada termina
> en un evento con la transcripción y la señalización de telefonía. Construir
> con LangGraph un sistema que recibe esos eventos de uno en uno, clasifica
> cómo fue la llamada y decide la próxima acción a tomar por nuestro sistema,
> emitiendo órdenes contra el CRM. En Python y con LangGraph.

Con el zip del reto (enunciado, catálogo de casos, esquemas, 16 eventos de
ejemplo) ya descargado en local, y una API key de OpenAI con tope de gasto
pendiente de llegar por correo.

## Instrucciones de proceso, en orden

1. Pedido explícito de un plan de trabajo y los primeros pasos antes de tocar
   código.
2. Confirmación de que el zip ya estaba en el directorio de trabajo
   (`reto-kontaktu.zip`) y orden de extraerlo.
3. URL del repositorio GitHub de destino:
   `https://github.com/chrisCs99/kontaktu-reto-langgraph.git`.
4. Antes de escribir ninguna línea de la solución: subir el primer commit
   (contenido íntegro del zip, sin tocar) a ese repositorio.
5. Corrección de rama por defecto: el repo se creó en `master`; se pidió
   renombrar a `main` antes de continuar.
6. Elección de modo de trabajo, tras preguntar explícitamente por las
   implicaciones de que esto es una prueba técnica de entrevista: autonomía
   para implementar, pero con consulta en los puntos donde el criterio del
   candidato pueda cambiar algo importante — no una implementación 100%
   desatendida.
7. Pedido de detener la implementación y explicar, antes de seguir, el mapeo
   completo de cada requisito (R1-R8) y regla de negocio (N1-N5) del
   enunciado contra la arquitectura propuesta, más el paso a paso de dónde
   estaba el desarrollo en ese momento.
8. Verificación explícita de que el requisito de entrega "prompts que use tu
   código, versionados en el repo" estaba realmente cubierto (llevó a
   confirmar `prompts/clasificar_llamada.md` y a crear este fichero).
9. Aporte de una vía de prueba durante la espera de la clave de OpenAI: uso
   de una API key personal de Google AI Studio (Gemini) contra la capa de
   compatibilidad OpenAI de Gemini, aceptando la condición de que el
   entregable final se queda en OpenAI (restricción explícita del
   enunciado) y que ese uso de Gemini es solo para pruebas locales.
10. Pedido de poder repetir esa prueba en local él mismo, viendo el detalle
    completo de lo implementado — resuelto añadiendo soporte opcional a
    `OPENAI_BASE_URL` en `kontaktu/llm.py` (sin efecto si no se define; con
    la API real de OpenAI por defecto), en vez de un script aparte no
    versionado.

## Criterios de calidad repetidos a lo largo de la sesión

- Trazabilidad de cada decisión de negocio hasta el requisito o regla
  concreta del enunciado que la motiva (para poder defenderlo a bajo nivel
  en la entrevista).
- Preferencia por verificar contra el `ejemplo-resuelto/` dado antes de
  confiar en la lógica propia.
- No dar nada por asumido sin haberlo probado: se insistió en validar tanto
  la rama determinista (contra `ejemplo-resuelto/`) como la rama LLM (con
  mock primero, con un modelo real después) antes de dar el sistema por
  terminado.
