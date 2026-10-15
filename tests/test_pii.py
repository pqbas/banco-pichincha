import pytest

from app.services.pii import enmascarar_pii


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("mi cédula es 1712345678", "mi cédula es <DNI>"),
        ("tarjeta 4111 1111 1111 1111 robada", "tarjeta <TARJETA> robada"),
        ("escríbanme a juan.perez@mail.com", "escríbanme a <CORREO>"),
        ("llamar al 0991234567", "llamar al <TELEFONO>"),
    ],
)
def test_enmascara_pii(texto, esperado):
    assert enmascarar_pii(texto) == esperado


def test_texto_sin_pii_no_cambia():
    texto = "el pago del 3 de octubre fue por 10.99"
    assert enmascarar_pii(texto) == texto
