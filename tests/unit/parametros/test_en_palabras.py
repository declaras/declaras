"""Que ningún paso del cálculo le llegue al titular en lenguaje de contador.

La comprobación importante es la primera: recorre el orden real del borrador y falla si el motor
tiene un nodo que nadie tradujo. Sin ella, agregar una casilla al motor deja al titular leyendo
"INCRNGO" sin que nada avise, que es el modo de falla silencioso que `en_palabras.py` cierra.
"""

from __future__ import annotations

import pytest

from declaras.parametros.casillas import (
    EN_PALABRAS_CASILLA,
    NOMBRES_DE_CASILLA,
    nombre_de_casilla,
)
from declaras.parametros.en_palabras import EN_PALABRAS, en_palabras
from declaras.render import ORDEN_CASILLAS

# Lo que un contador dice y un titular no entiende. Si una de estas aparece en un nombre "en
# palabras", la traducción no se hizo: se copió.
JERGA = (
    "incrngo",
    "rlg",
    "uvt",
    "cédula",
    "cedula",
    "art.",
    "artículo",
    "esal",
    "renta líquida",
    "renta liquida",
    "base gravable",
    "retención en la fuente",
    "retencion en la fuente",
    "procedentes",
    "exentas",
)


def test_ningun_paso_del_borrador_queda_sin_traducir() -> None:
    sin_traducir = [c for c in ORDEN_CASILLAS if c not in EN_PALABRAS]
    assert not sin_traducir, (
        "Estos nodos del motor no tienen nombre para el titular y le van a salir con la etiqueta "
        f"técnica: {sin_traducir}. Agrégalos a EN_PALABRAS en declaras/parametros/en_palabras.py."
    )


def test_no_se_traduce_nada_que_el_motor_no_calcule() -> None:
    """Un código de más es una traducción que nunca se muestra, o un typo en el código."""
    sobrantes = sorted(set(EN_PALABRAS) - set(ORDEN_CASILLAS))
    assert not sobrantes, (
        f"Estos códigos no están en ORDEN_CASILLAS y nadie los va a ver: {sobrantes}. "
        "O el motor los quitó, o el código está mal escrito."
    )


@pytest.mark.parametrize("codigo", sorted(EN_PALABRAS))
def test_el_nombre_para_el_titular_no_lleva_jerga(codigo: str) -> None:
    nombre = EN_PALABRAS[codigo].lower()
    encontrada = [j for j in JERGA if j in nombre]
    assert not encontrada, (
        f"{codigo} dice {EN_PALABRAS[codigo]!r}, que trae {encontrada}. El titular declara una vez "
        "al año; esa palabra la reconoce un contador."
    )


@pytest.mark.parametrize("codigo", sorted(EN_PALABRAS))
def test_los_nombres_estan_escritos_como_prosa(codigo: str) -> None:
    nombre = EN_PALABRAS[codigo]
    assert nombre == nombre.strip(), f"{codigo} tiene espacios sobrantes"
    assert nombre[0].isupper() or nombre[0] == "¿", (
        f"{codigo} dice {nombre!r} y no arranca en mayúscula: se va a ver como una nota, no como "
        "el nombre de una cifra."
    )
    assert not nombre.endswith("."), (
        f"{codigo} dice {nombre!r}: es el nombre de una fila, no una oración."
    )


def test_el_respaldo_es_la_etiqueta_tecnica_y_no_un_texto_vacio() -> None:
    """Si aparece un nodo nuevo, es mejor jerga correcta que un texto que no dice nada."""
    assert en_palabras("NODO_QUE_NO_EXISTE", "Etiqueta del motor") == "Etiqueta del motor"


def test_el_saldo_no_se_compromete_con_un_signo() -> None:
    """El mismo nodo dice "me toca pagar" y "me devuelven"; el nombre no puede elegir uno."""
    nombre = EN_PALABRAS["SALDO"].lower()
    assert "a pagar" in nombre and "a favor" in nombre, (
        f"SALDO dice {EN_PALABRAS['SALDO']!r}. Puede ser negativo, así que el nombre tiene que "
        "cubrir los dos casos o le va a decir 'lo que debes' a alguien a quien le devuelven."
    )


# ── los renglones del formulario 210 ──────────────────────────────────────────────────────────────

# Los renglones que la sugerencia de la DIAN reportó en un caso real. No son todos los que puede
# reportar, pero son los que se vieron, y cada uno que aparezca nuevo se agrega aquí: es la lista
# que impide que la traducción se quede atrás del formulario sin que nadie lo note.
RENGLONES_QUE_LA_DIAN_REPORTA = (29, 30, 32, 33, 36, 43, 51, 58, 59, 67, 74, 76, 84, 100, 131, 132)


@pytest.mark.parametrize("numero", RENGLONES_QUE_LA_DIAN_REPORTA)
def test_los_renglones_reportados_tienen_nombre_para_el_titular(numero: int) -> None:
    assert numero in EN_PALABRAS_CASILLA, (
        f"El renglón {numero} lo reporta la sugerencia de la DIAN y al titular le sale con el "
        f"nombre oficial ({nombre_de_casilla(numero)!r}). Agrégalo a EN_PALABRAS_CASILLA."
    )


@pytest.mark.parametrize("numero", sorted(EN_PALABRAS_CASILLA))
def test_el_renglon_en_palabras_no_lleva_jerga(numero: int) -> None:
    nombre = EN_PALABRAS_CASILLA[numero].lower()
    # "AFC", "AVC" y "pensión voluntaria" se quedan: son los nombres comerciales de los productos
    # que el titular tiene contratados y con los que su banco se los factura. Traducirlos le
    # impediría reconocer el suyo.
    encontrada = [j for j in JERGA if j in nombre]
    assert not encontrada, (
        f"El renglón {numero} dice {EN_PALABRAS_CASILLA[numero]!r}, que trae {encontrada}."
    )


@pytest.mark.parametrize("numero", sorted(EN_PALABRAS_CASILLA))
def test_no_se_traduce_un_renglon_que_no_existe_en_el_formulario(numero: int) -> None:
    """Un número inventado es una fila que nunca se pinta, o un typo con consecuencias.

    Los renglones 136 a 139 son el caso de aviso: transcribí el saldo en 138 y 139, que en el
    formulario real son el número de dependientes y su adición. El formulario habría puesto el
    saldo a pagar en la casilla del conteo de dependientes.
    """
    assert numero in NOMBRES_DE_CASILLA, (
        f"El renglón {numero} no existe en el formulario 210 v18. Verifícalo contra el formulario, "
        "no contra la memoria."
    )


def test_el_saldo_no_quedo_en_la_casilla_de_los_dependientes() -> None:
    """El error que ya cometí una vez, fijado para que no vuelva."""
    assert EN_PALABRAS_CASILLA[136] == "Lo que te falta pagar"
    assert EN_PALABRAS_CASILLA[137] == "Lo que te devuelven"
    assert "depend" in EN_PALABRAS_CASILLA[138].lower()
    assert 139 not in EN_PALABRAS_CASILLA, (
        "139 es la adición por dependientes a la casilla 92, no una cifra que se lea suelta."
    )
