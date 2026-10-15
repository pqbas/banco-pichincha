"""Evaluación genérica por HTTP contra eval/golden.jsonl, configurada en eval/config.json.

- Clasificación con scikit-learn (si config.clase existe)
- Retrieval con Ragas IDBasedContextPrecision y Recall (si config.ids existe)
- Faithfulness con Ragas y Claude como juez (si config.faithfulness existe)
- Operación: latencia, y tokens y costo si la respuesta trae uso

Rutas a campos de la respuesta: "a.b", "lista[].campo", "lista[]" (cada elemento como JSON).
Golden: una línea JSON por caso con "id", los campos de config.entrada y los esperados (config.*.golden).

Uso: python -m eval.evaluar [--repeticiones N]
  EVAL_ENDPOINT sobreescribe config.endpoint, EVAL_CONFIG apunta a otro archivo de configuración
"""

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv

DIR = Path(__file__).resolve().parent
load_dotenv(DIR.parent / ".env")


def extraer(dato, ruta: str):
    """Sigue una ruta tipo 'a.b', 'lista[].campo' o 'lista[]' y devuelve un valor o una lista."""
    if not ruta:
        return dato
    cabeza, _, resto = ruta.partition(".")
    if cabeza.endswith("[]"):
        lista = (dato or {}).get(cabeza[:-2]) or []
        return [extraer(x, resto) for x in lista] if resto else lista
    return extraer((dato or {}).get(cabeza), resto)


def como_lista(valor) -> list:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def como_texto(valor) -> str:
    return valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)


def verificar_endpoint(endpoint: str) -> None:
    """Falla con un mensaje claro si la API no está levantada."""
    try:
        httpx.options(endpoint, timeout=5)
    except httpx.ConnectError:
        raise SystemExit(
            f"No hay nada escuchando en {endpoint}.\n"
            "Levanta la API en otra terminal con `make run`, o pasa la URL desplegada con EVAL_ENDPOINT=<url>."
        )


def correr_sistema(config: dict, casos: list[dict], repeticiones: int) -> list[dict]:
    endpoint = os.getenv("EVAL_ENDPOINT") or config["endpoint"]
    verificar_endpoint(endpoint)
    filas = []
    with httpx.Client(timeout=120) as cliente:
        for c in casos:
            for i in range(repeticiones):
                inicio = time.perf_counter()
                r = cliente.post(endpoint, json={k: c[k] for k in config["entrada"]}, headers=config.get("headers", {}))
                latencia = time.perf_counter() - inicio
                cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                filas.append({"id": c["id"], "repeticion": i, "status": r.status_code, "latencia_s": latencia, "caso": c, "respuesta": cuerpo})
                clase = config.get("clase")
                if clase:
                    predicho = extraer(cuerpo, clase["respuesta"]) if r.is_success else f"error_{r.status_code}"
                    marca = "OK " if predicho == c[clase["golden"]] else "MAL"
                    print(f"  {marca} {c['id']} esperado={c[clase['golden']]:<14} predicho={predicho}")
                else:
                    print(f"  {c['id']} status={r.status_code} {latencia:.2f}s")
    return filas


async def metricas_ragas(config: dict, filas: list[dict]) -> None:
    from anthropic import AsyncAnthropic
    from ragas.dataset_schema import SingleTurnSample
    from ragas.llms import llm_factory
    from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    from ragas.metrics.collections import Faithfulness

    ids, faith = config.get("ids"), config.get("faithfulness")
    faithfulness = None
    if faith:
        juez = llm_factory(config.get("juez", "claude-haiku-5-5"), provider="anthropic",
                           client=AsyncAnthropic(timeout=120, max_retries=5), max_tokens=4000)
        # Los modelos actuales de Claude no aceptan parámetros de muestreo
        for k in ("temperature", "top_p"):
            juez.model_args.pop(k, None)
        faithfulness = Faithfulness(llm=juez)
    precision, recall = IDBasedContextPrecision(), IDBasedContextRecall()
    semaforo = asyncio.Semaphore(config.get("concurrencia_juez", 4))

    async def una(f: dict) -> None:
        cuerpo, caso = f["respuesta"], f["caso"]
        # Los rechazos de la validación (4xx) no tienen recuperados ni respuesta que medir
        if f["status"] >= 400:
            return
        if ids and cuerpo:
            esperados = set(map(str, como_lista(caso[ids["golden"]])))
            if ids.get("recuperados"):
                muestra = SingleTurnSample(
                    retrieved_context_ids=[str(x) for x in como_lista(extraer(cuerpo, ids["recuperados"]))],
                    reference_context_ids=sorted(esperados),
                )
                f["context_precision"] = await precision.single_turn_ascore(muestra)
                f["context_recall"] = await recall.single_turn_ascore(muestra)
            if ids.get("citados"):
                citados = set(map(str, como_lista(extraer(cuerpo, ids["citados"]))))
                # cubren: cita todos los esperados; exactos: además no cita de más
                f["citados_cubren"] = esperados <= citados
                f["citados_exactos"] = esperados == citados
        respuesta = extraer(cuerpo, faith["respuesta"]) if faith and cuerpo else None
        if faithfulness and respuesta:
            pregunta = caso.get(faith["pregunta"], "")
            contextos = [f"Pregunta o entrada del usuario: {pregunta}"] + [
                como_texto(x) for ruta in faith["contextos"] for x in como_lista(extraer(cuerpo, ruta))
            ]
            async with semaforo:
                r = await faithfulness.ascore(user_input=pregunta, response=como_texto(respuesta), retrieved_contexts=contextos)
            f["faithfulness"] = r.value

    await asyncio.gather(*(una(f) for f in filas))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeticiones", type=int, default=1)
    args = parser.parse_args()

    config = json.loads(Path(os.getenv("EVAL_CONFIG") or DIR / "config.json").read_text())
    casos = [json.loads(l) for l in (DIR / "golden.jsonl").read_text().splitlines() if l.strip()]
    print(f"Golden: {len(casos)} casos, {args.repeticiones} repetición(es), endpoint {os.getenv('EVAL_ENDPOINT') or config['endpoint']}\n")

    filas = correr_sistema(config, casos, args.repeticiones)
    if config.get("ids") or config.get("faithfulness"):
        print("\nCalculando métricas con Ragas...")
        asyncio.run(metricas_ragas(config, filas))
    df = pd.DataFrame(filas)
    resumen: dict = {"exitosas": float((df["status"] < 400).mean())}

    clase = config.get("clase")
    if clase:
        from sklearn.metrics import classification_report, confusion_matrix

        esperado = df["caso"].map(lambda c: c[clase["golden"]])
        predicho = df.apply(lambda f: extraer(f["respuesta"], clase["respuesta"]) if f["status"] < 400 else f"error_{f['status']}", axis=1)
        etiquetas = sorted(set(esperado))
        todas = etiquetas + sorted(set(predicho) - set(etiquetas))
        reporte = classification_report(esperado, predicho, labels=etiquetas, output_dict=True, zero_division=0)
        matriz = pd.DataFrame(confusion_matrix(esperado, predicho, labels=todas), index=todas, columns=todas).loc[etiquetas]
        df["predicho"] = predicho
        consistencia = df.groupby("id")["predicho"].agg(lambda s: Counter(s).most_common(1)[0][1] / len(s)).mean()
        resumen.update(accuracy=float((esperado == predicho).mean()), f1_macro=reporte["macro avg"]["f1-score"], consistencia=float(consistencia))
        print("\nClasificación (scikit-learn)")
        print(classification_report(esperado, predicho, labels=etiquetas, zero_division=0))
        print("Matriz de confusión (filas esperado, columnas predicho)")
        print(matriz.to_string())
        if clase.get("critica") in reporte:
            resumen["recall_clase_critica"] = reporte[clase["critica"]]["recall"]
            print(f"\nRecall de la clase crítica '{clase['critica']}': {resumen['recall_clase_critica']:.2f}")
        print(f"Consistencia entre repeticiones: {consistencia:.2f}")

    if "context_recall" in df:
        k = int(df["respuesta"].map(lambda r: len(como_lista(extraer(r, config["ids"]["recuperados"])))).max() or 1)
        resumen.update(context_precision=float(df["context_precision"].mean()), context_recall=float(df["context_recall"].mean()))
        print(f"\nRetrieval (Ragas, k={k}, con un id relevante la precision máxima es {1 / k:.2f})")
        print(f"  context precision {resumen['context_precision']:.2f}   context recall {resumen['context_recall']:.2f}")
        fallos = sorted(df.loc[df["context_recall"] == 0, "id"].unique())
        if fallos:
            print(f"  sin el documento esperado en lo recuperado: {fallos}")

    if "citados_cubren" in df:
        resumen.update(citados_cubren=float(df["citados_cubren"].mean()), citados_exactos=float(df["citados_exactos"].mean()))
        print(f"\nIds citados\n  cubren los esperados {resumen['citados_cubren']:.2f}   exactos (sin citar de más) {resumen['citados_exactos']:.2f}")
        fallos = sorted(df.loc[df["citados_cubren"] == False, "id"].unique())  # noqa: E712
        if fallos:
            print(f"  no citan todo lo esperado: {fallos}")

    if "faithfulness" in df:
        resumen["faithfulness"] = float(df["faithfulness"].mean())
        print(f"\nFaithfulness (Ragas): {resumen['faithfulness']:.2f}")
        for _, f in df[df["faithfulness"] < 0.7].sort_values("faithfulness").iterrows():
            print(f"  {f['id']} {f['faithfulness']:.2f}  {como_texto(extraer(f['respuesta'], config['faithfulness']['respuesta']))[:140]}...")

    lat = df["latencia_s"]
    resumen.update(latencia_p50_s=float(lat.median()), latencia_p95_s=float(lat.quantile(0.95)))
    print(f"\nOperación\n  latencia p50 {resumen['latencia_p50_s']:.2f}s   p95 {resumen['latencia_p95_s']:.2f}s")
    uso, precio = config.get("uso"), config.get("precio_usd_por_millon")
    if uso:
        ent = sum(extraer(r, uso["entrada"]) or 0 for r in df["respuesta"])
        sal = sum(extraer(r, uso["salida"]) or 0 for r in df["respuesta"])
        if ent or sal:
            costo = (ent * precio["entrada"] + sal * precio["salida"]) / 1e6 if precio else None
            resumen.update(tokens_entrada=ent, tokens_salida=sal, costo_usd=costo)
            print(f"  tokens {ent} entrada / {sal} salida" + (f"   costo {costo:.4f} USD ({costo / len(df):.5f} por request)" if costo is not None else ""))

    salida = DIR / "resultados" / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "repeticiones": args.repeticiones,
                                  "resumen": resumen, "detalle": df.drop(columns=["caso"]).to_dict(orient="records")},
                                 ensure_ascii=False, indent=1, default=str))
    print(f"\nReporte en {salida.relative_to(DIR.parent)}")


if __name__ == "__main__":
    main()
