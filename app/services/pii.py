import re

# El orden importa, de lo más largo a lo más corto para que una tarjeta no se lea como cédula.
# Un celular (09 + 8 dígitos) y una cédula de Guayas (provincia 09) son indistinguibles por regex,
# como ambos son PII se enmascaran igual. Ajustar los patrones al país del caso.
PATRONES = [
    ("CORREO", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("TARJETA", re.compile(r"\b\d(?:[ -]?\d){12,15}\b")),
    ("TELEFONO", re.compile(r"(?:\+593\s?)?\b09\d{8}\b|\+\d{1,3}\s?\d{6,10}\b")),
    ("DNI", re.compile(r"\b\d{10}\b|\b\d{8}\b")),
]


def enmascarar_pii(texto: str) -> str:
    for etiqueta, patron in PATRONES:
        texto = patron.sub(f"<{etiqueta}>", texto)
    return texto
