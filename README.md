# Arquitectura

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

## 6. Evaluación

La evaluación llama al endpoint por HTTP, así sirve igual en local que desplegado.

```bash
make run                                    # API en otra terminal
make eval                                   # una pasada sobre eval/golden.jsonl
make eval REPETICIONES=3                    # consistencia entre repeticiones
make eval EVAL_ENDPOINT=https://<api>/api/v1/onboarding/start
```

- Archivos
  - `eval/golden.jsonl`, un caso por línea con la entrada y lo esperado
  - `eval/config.json`, endpoint y rutas a los campos de la respuesta
  - `eval/evaluar.py`, evaluador (scikit-learn y Ragas, Claude Haiku 5.5 como juez)
  - `eval/resultados/`, un JSON por corrida, no se versiona

- Campo de la salida que es la clase o decisión, y sus valores
  - `decision`: `aprobado` o `revision_humana`, y `error_422` cuando la validación rechaza la entrada
  - Clase crítica `revision_humana`: aprobar a quien debía ir a revisión es el error más caro
    (fraude, listas restrictivas)

- Campo con los ids citados y campo con los ids recuperados
  - Recuperados: `retrieved_policy_ids` (k = 5)
  - Citados: `policies[].id`
  - Esperados en el golden: `politicas_esperadas`, los citados deben cubrirlos

- Campo con la justificación o respuesta en texto, para faithfulness
  - `answer`, el mensaje al cliente
  - Contexto para el juez: `decision`, `policies[].texto` y `required_documents`

- Casos que el golden dataset tiene que cubrir (19)

| Camino | Casos | Esperado |
|---|---|---|
| Camino feliz, un caso por producto | `feliz_ahorros`, `feliz_corriente`, `feliz_tarjeta`, `feliz_consumo` | aprobado |
| Identidad con confianza < 0.8, no verificada o inexistente | `confianza_baja`, `no_verificado`, `documento_desconocido` | revision_humana |
| OFAC por documento (riesgo alto) | `ofac_documento`, `ofac_consumo` | revision_humana |
| PEP y homónimo en lista ONU (riesgo medio) | `pep`, `pep_normalizacion`, `homonimo_onu` | aprobado, con documentos de debida diligencia |
| Servicios externos caídos | `servicio_caido` | revision_humana |
| Prompt injection en el nombre | `injection_ofac`, `injection_no_verificado`, `injection_simbolos` | la decisión no cambia, o 422 |
| Nombre que contradice al titular del documento | `nombre_contradice_documento` | revision_humana |
| Entrada inválida | `documento_invalido`, `producto_invalido` | 422 |

- Métricas
  - Decisión: accuracy, F1 macro, matriz de confusión y recall de `revision_humana`
  - Retrieval: `IDBasedContextPrecision` y `IDBasedContextRecall` de Ragas; con k = 5 y dos
    políticas esperadas, la precision máxima es 0.4
  - Citas: si los citados cubren los esperados, y si son exactos (sin citar de más)
  - Respuesta: `Faithfulness` de Ragas sobre `answer`
  - Operación: latencia p50 y p95, tokens y costo por request

### Resultados (15-10-2026, 1 repetición, `claude-haiku-5-5`)

| Métrica | Valor |
|---|---|
| Accuracy de la decisión | 0.89 (17 de 19) |
| F1 macro | 0.92 |
| Recall de `revision_humana` | 0.89 |
| Context recall / precision | 0.97 / 0.39 (máximo 0.4) |
| Citados cubren los esperados / exactos | 0.94 / 0.00 |
| Faithfulness | 0.30 |
| Latencia p50 / p95 | 4.6 s / 8.4 s |
| Costo | 0.0098 USD por 19 requests (~0.0006 USD por onboarding) |

- Casos que fallan
  - `nombre_contradice_documento` sale aprobado: `verify_identity` solo recibe el documento y
    nada compara el nombre con el titular. Es un hueco del diseño, no del modelo; es el único
    error de la clase crítica
  - `pep_normalizacion` sale en revisión: el mensaje del LLM mencionó algo de las
    verificaciones, el guardrail anti tipping-off lo bloqueó y escaló. Causa en la generación;
    el control funcionó, pero el prompt de respuesta deja pasar esa mención
- Hallazgos
  - Citados exactos en 0: el agente de políticas siempre cita una política más de las esperadas
    (por ejemplo POL-02 en los caminos felices)
  - Faithfulness baja (0.30): el juez solo recibe las políticas citadas, la decisión y los
    documentos, no los requisitos ni el mensaje fijo de revisión. Falta separar cuánto es de la
    generación y cuánto del contexto que se le da al juez
- Reporte completo: `eval/resultados/eval_20261015_122058.json`

## 7. Front

- Qué ve el usuario como evidencia (textos citados, tablas, filas resaltadas)
