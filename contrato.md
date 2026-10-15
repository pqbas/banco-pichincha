# Contrato del caso

<!-- Lo diseña la persona, no el agente. Cada parte de la skill genai lee este archivo. -->

## 1. Caso

- Objetivo
- Usuarios (y quién no lo usa)
- Riesgos
- Valor (cómo se mide)

## 2. API

- Método y ruta (ejemplo `POST /<recurso>/<accion>`)
- Entrada, campo, tipo y validación de cada uno
- Salida, campo, tipo y significado de cada uno
- Errores, status y cuándo (ejemplo 404 si el recurso no existe)

## 3. Fuentes de datos

- Qué datos se consultan, de dónde salen y con qué clave (ejemplo registros del usuario por su id de sesión)
- Qué documentos van al retrieval y cuántos se recuperan (k)
- Qué identifica al usuario o cliente, y de dónde sale (nunca del LLM)

## 4. Workflow

- Pasos en orden, indicando cuáles son código y cuál es la llamada al LLM
- Qué recibe el LLM y qué schema devuelve

## 5. Validaciones y guardrails

- Qué se valida de la salida del LLM contra el contexto entregado
- Cuándo se marca para revisión humana
- PII que se enmascara

## 6. Evaluación

- Campo de la salida que es la clase o decisión, y sus valores
- Campo con los ids citados y campo con los ids recuperados
- Campo con la justificación o respuesta en texto, para faithfulness
- Casos que el golden dataset tiene que cubrir

## 7. Front

- Qué ve el usuario como evidencia (textos citados, tablas, filas resaltadas)
