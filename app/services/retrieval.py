"""Búsqueda léxica BM25 sobre documentos con id y texto, sin depender de embeddings.

En la defensa: en producción sería híbrida (BM25 + embeddings, por ejemplo Titan) con reranker.
"""

import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from rank_bm25 import BM25Okapi

D = TypeVar("D")


def tokenizar(texto: str) -> list[str]:
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    # Stemming crudo por prefijo, para que variantes de una palabra ("pagaron", "pago") coincidan
    return [t[:5] for t in re.findall(r"[a-z0-9]+", texto)]


class Buscador(Generic[D]):
    def __init__(self, documentos: Sequence[D], texto_de: Callable[[D], str]):
        self.documentos = list(documentos)
        self._bm25 = BM25Okapi([tokenizar(texto_de(d)) for d in self.documentos])

    def buscar(self, consulta: str, k: int = 3) -> list[D]:
        puntajes = self._bm25.get_scores(tokenizar(consulta))
        orden = sorted(range(len(self.documentos)), key=lambda i: puntajes[i], reverse=True)
        return [self.documentos[i] for i in orden[:k]]
