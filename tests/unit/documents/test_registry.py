"""La declaracion del anio anterior y el borrador del anio en curso son el mismo formulario,
asi que los dos tienen que quedar cubiertos: si uno se queda sin lector, la consola lo
muestra como un documento que no se pudo leer."""

from __future__ import annotations

from declaras.documents.parsers import renta_210
from declaras.documents.registry import DETERMINISTIC_READERS


def test_las_dos_declaraciones_usan_el_lector_del_210():
    assert DETERMINISTIC_READERS["PRIOR_RETURN"] is renta_210.parse
    assert DETERMINISTIC_READERS["SUGGESTED_RETURN"] is renta_210.parse


def test_todos_los_documentos_que_baja_el_portal_tienen_lector():
    """Sin esto, un documento nuevo del portal aparece en la consola como "no se pudo leer"
    y nadie se entera hasta que un cliente lo ve."""
    from declaras.adapters.dian.rest.flows import DOWNLOADERS

    for doc_type in DOWNLOADERS:
        assert doc_type.value in DETERMINISTIC_READERS, f"{doc_type.value} se quedaria sin lector"
