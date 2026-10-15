# Diseño del caso

## Arquitectura

- 1 UI

- 1 db para registrar los onboardings en dynamodb

- Lambda con langgraph para implmentar flujo multi agentico:
  - Agente orquestador
    - controla las tools de cada sub agente
    - manejar el estado entre pasos
    - mitigar incertidumbre

  - Agente verificar identidad
  - Agente consulas de listas de riesgo
  - Agente consulta de politicas
  - Generación de respuestas al cliente

- Tools:
  - Tool 1 (mockear): verify_identity(document_id) Retorna {"verified":
    true/false, "confidence": 0.0-1.0}. Si confianza < 0.8, requiere
    escalamiento

  - Tool 2 (mockear) check_risk_lists(name, document_id) Retorna {"risk_level":
    "low/medium/high", "matches": []}. Si high, requiere escalamiento

  - Tool 3 (mockear) prepare_documentation(product, client_data) Retorna
    documentos requeridos

## 1. Caso

- Objetivo
  - Onboarding de clientes

- Usuarios
  - Clientes del banco

- Riesgos
  - Fraude, prompt injection, PII, un agente podría usar una tool no permitida

- Valor (cómo se mide)
  - Reducir el tiempo de onboarding

## 2. API

- Método y ruta (ejemplo `POST /<recurso>/<accion>`)

  - POST /api/v1/onboarding/start recibe {"prospect_name": "Juan Perez",
    "document_id": "1712345678", "product": "cuenta_ahorros"}

- Entrada, campo, tipo y validación de cada uno

- Salida, campo, tipo y significado de cada uno
- Errores, status y cuándo (ejemplo 404 si el recurso no existe)

## 3. Fuentes de datos --> API Externa, se mockea

- Qué datos se consultan, de dónde salen y con qué clave (ejemplo registros del
  usuario por su id de sesión)
- Qué documentos van al retrieval y cuántos se recuperan (k)
  - Las politicas del banco
  - 5 politicas

- Qué identifica al usuario o cliente, y de dónde sale (nunca del LLM)
  - DNI
  - Nombre

## 4. Workflow

- Pasos en orden, indicando cuáles son código y cuál es la llamada al LLM

  - Agente orquestador recibe el json inicial e inicia cada sub agente con
    información necesaria para su caso de uso especifico
  - Agente de verificación de identidad ejecuta tool 1 (verify identity)
  - Agente de consultas de riesgo ejecuta tool 2 (risk list)
  - Agente de consutlas de politicas ejecuta tool 3 (get_policy)
  - Agente orquestador recibe y genera una respuesta

## 5. Validaciones y guardrails

- Qué se valida de la salida del LLM contra el contexto entregado
- Cuándo se marca para revisión humana
- PII que se enmascara

## 7. Front

- Qué ve el usuario como evidencia (textos citados, tablas, filas resaltadas)
