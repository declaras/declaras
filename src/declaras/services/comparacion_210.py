"""El borrador que la DIAN precargó contra el que salió del cálculo, casilla por casilla.

POR QUÉ ESTO IMPORTA MÁS DE LO QUE PARECE. La DIAN precrea un borrador del 210 con lo que los
terceros le reportaron, y el contribuyente puede firmarlo tal cual en tres clics. Ese borrador es
una sugerencia: la propia DIAN lo dice ("solo usted conoce la realidad jurídica, económica y
financiera que debe declarar"). Pero se ve oficial, y esa es la trampa.

Las dos cifras difieren siempre que el trabajo con documentos aporte algo: un certificado de
intereses de vivienda que la DIAN no ve, un ingreso ajeno que se marcó como de otra persona, unas
cesantías que quedaron exentas. La diferencia es exactamente el valor del producto, y hasta ahora no
se mostraba en ninguna parte: había que confiar en que el número final era mejor.

También va al revés y es más grave: si NUESTRA casilla es menor que la de la DIAN sin una razón
registrada, hay un ingreso que se perdió por el camino. Ese es el caso que la DIAN cruza sola.

LAS DOS MITADES YA EXISTÍAN. El parser del formulario 210 lee el PDF del borrador de la DIAN y saca
sus casillas (`documents/parsers/renta_210.py`, verificado casilla por casilla contra una
declaración real), y `render/formulario_210` produce las nuestras desde la liquidación. Este módulo
solo las empareja; no calcula nada.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, computed_field

from declaras.documents.models import DocumentReading
from declaras.parametros.casillas import casilla_en_palabras, nombre_de_casilla
from declaras.render import Casilla

# Prefijo con el que el lector del 210 nombra cada casilla que encuentra: `casilla_32`.
_PREFIJO = "casilla_"


class Diferencia(StrEnum):
    """Qué relación hay entre las dos cifras de una casilla."""

    IGUALES = "IGUALES"
    # La nuestra es mayor. Normal en ingresos (aparece algo que la DIAN no vio) y sospechoso en
    # deducciones solo si nadie registró de dónde salió.
    MAYOR_NUESTRA = "MAYOR_NUESTRA"
    # La nuestra es menor. En una casilla de ingresos esto es lo que hay que poder explicar: la DIAN
    # tiene un dato que nosotros no estamos declarando.
    MENOR_NUESTRA = "MENOR_NUESTRA"
    # La casilla existe en un lado y no en el otro.
    SOLO_NUESTRA = "SOLO_NUESTRA"
    SOLO_DE_LA_DIAN = "SOLO_DE_LA_DIAN"


class CasillaComparada(BaseModel):
    """Una casilla del 210 vista desde los dos lados."""

    numero: int
    nombre: str
    en_palabras: str
    # `None` significa que ese lado no trae la casilla, que es distinto de traerla en cero.
    nuestra: int | None
    de_la_dian: int | None
    diferencia: Diferencia

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta(self) -> int:
        """Cuánto cambia esta casilla respecto de lo que la DIAN precargó.

        Un lado ausente se trata como cero SOLO para restar: la relación real ya está en
        `diferencia`, y mezclar "no está" con "vale cero" ahí sería perder la distinción.
        """
        return (self.nuestra or 0) - (self.de_la_dian or 0)


class Contra(StrEnum):
    """Contra qué se está comparando el formulario del cálculo.

    Son dos preguntas distintas y no se pueden confundir en la pantalla:

      el borrador de la DIAN   ¿en qué difiere de lo que la DIAN precargó con lo que ella sabe?
                               Las diferencias son lo que aportó el trabajo con documentos.
      la presentada            ¿en qué difiere de lo que se declaró de verdad ese año?
                               Casi siempre es lo que hizo un contador, así que la diferencia es
                               una segunda opinión sobre su trabajo, y puede ir en cualquier
                               dirección: plata que dejó sobre la mesa, o un error nuestro.
    """

    BORRADOR_DE_LA_DIAN = "BORRADOR_DE_LA_DIAN"
    DECLARACION_PRESENTADA = "DECLARACION_PRESENTADA"


class Comparacion210(BaseModel):
    """El formulario del cálculo contra otro formulario 210.

    `disponible` en False significa que no hay con qué comparar (no existe ese documento en el
    expediente, o el PDF no se pudo leer). No es un error: para el borrador de la DIAN es el caso de
    quien declara por primera vez, y para la presentada es el de un año que todavía no se declaró.
    """

    contra: Contra
    disponible: bool
    casillas: list[CasillaComparada]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def con_diferencia(self) -> list[CasillaComparada]:
        """Las casillas donde la cifra cambia de verdad, ordenadas por cuánta plata mueven.

        FILTRA POR `delta` Y NO POR `diferencia`, y eso importa: medido contra un caso real, la
        comparación cruda daba 77 casillas "distintas" de 80, casi todas porque un lado no trae la
        casilla y el otro la trae en cero. Una lista de 77 filas donde 73 no mueven un peso no se
        lee, y las cuatro que sí importaban quedaban enterradas.

        La relación estructural sigue en `diferencia` para quien la necesite.
        """
        mueven = [c for c in self.casillas if c.delta != 0]
        return sorted(mueven, key=lambda c: (-abs(c.delta), c.numero))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def menores_que_la_dian(self) -> list[CasillaComparada]:
        """Las casillas donde declaramos MENOS que el otro formulario.

        Es la mitad peligrosa cuando se compara contra el borrador de la DIAN: ella tiene un dato
        que no estamos declarando, y eso lo cruza sola. Cada una necesita una razón registrada (un
        ingreso ajeno, una cifra corregida con soporte) o es plata que falta.

        Contra una declaración presentada significa otra cosa: que el contador declaró más que
        nosotros en esa casilla, y ahí la pregunta es quién tiene razón.
        """
        return [c for c in self.con_diferencia if c.delta < 0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coinciden(self) -> bool:
        return self.disponible and not self.con_diferencia


def comparar(
    nuestras: list[Casilla],
    otro: DocumentReading | None,
    contra: Contra = Contra.BORRADOR_DE_LA_DIAN,
) -> Comparacion210:
    """Empareja las casillas de los dos formularios por número.

    Se comparan TODAS las casillas de los dos lados, no solo las nuestras: una casilla que el otro
    formulario trae y nosotros no es justamente la que hay que mirar, y filtrar por las nuestras la
    escondería.

    `contra` no cambia el cálculo, cambia lo que la comparación SIGNIFICA, y la pantalla necesita
    saberlo: una diferencia frente al borrador de la DIAN es normalmente una mejora nuestra, y una
    diferencia frente a lo que se presentó de verdad es una discrepancia con el trabajo de otro.
    """
    if otro is None:
        return Comparacion210(contra=contra, disponible=False, casillas=[])

    del_otro = _casillas_del_lector(otro)
    if not del_otro:
        return Comparacion210(contra=contra, disponible=False, casillas=[])

    mias = {c.numero: c.valor for c in nuestras}
    comparadas = [
        _comparar_una(numero, mias.get(numero), del_otro.get(numero))
        for numero in sorted(set(mias) | set(del_otro))
    ]
    return Comparacion210(contra=contra, disponible=True, casillas=comparadas)


def _comparar_una(numero: int, nuestra: int | None, dian: int | None) -> CasillaComparada:
    return CasillaComparada(
        numero=numero,
        nombre=nombre_de_casilla(numero),
        en_palabras=casilla_en_palabras(numero),
        nuestra=nuestra,
        de_la_dian=dian,
        diferencia=_relacion(nuestra, dian),
    )


def _relacion(nuestra: int | None, dian: int | None) -> Diferencia:
    if nuestra is None:
        return Diferencia.SOLO_DE_LA_DIAN
    if dian is None:
        return Diferencia.SOLO_NUESTRA
    if nuestra == dian:
        return Diferencia.IGUALES
    return Diferencia.MAYOR_NUESTRA if nuestra > dian else Diferencia.MENOR_NUESTRA


def _casillas_del_lector(reading: DocumentReading) -> dict[int, int]:
    """Las casillas que el lector del 210 sacó del PDF, por número.

    El lector las nombra `casilla_32`. Los campos que no siguen ese patrón (el año gravable, el
    número de formulario) no son casillas de cifra y se ignoran; los que no traen un entero
    tampoco, porque comparar contra un texto no significa nada.
    """
    casillas: dict[int, int] = {}
    for campo in reading.fields:
        if not campo.name.startswith(_PREFIJO):
            continue
        numero = campo.name.removeprefix(_PREFIJO)
        if not numero.isdigit() or not isinstance(campo.value, int):
            continue
        casillas[int(numero)] = campo.value
    return casillas
