import json

import pytest
from fastapi.testclient import TestClient

import declaras.api.main as api_main
from declaras.api import almacen
from declaras.api.main import app
from declaras.caso import Fuente, IngresoLaboral
from tests.golden.casos import g1

PDF = b"%PDF-fake"


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    return TestClient(app)


def _crear(cliente) -> str:
    r = cliente.post("/casos", json=g1().model_dump())
    assert r.status_code == 201
    return r.json()["id"]


def _laboral(confianza: float | None = 0.9, ref: str = "abc123def456") -> IngresoLaboral:
    return IngresoLaboral(
        empleador_nit="901", empleador_nombre="Otro Empleador",
        salarios=10_000_000, aportes_salud=400_000, aportes_pension=400_000,
        retencion=0, fuente=Fuente.documento("220", ref, confianza=confianza))


class ExtractorFalso:
    """Doble de `extraer_220`: registra la llamada y devuelve o revienta a pedido.

    Con la firma completa (`anio_esperado`) a propósito: si el endpoint dejara de
    pasar el año, el test que lo verifica falla en vez de pasar de largo.
    """

    def __init__(self, laboral: IngresoLaboral | None = None,
                 error: Exception | None = None):
        self.laboral = laboral if laboral is not None else _laboral()
        self.error = error
        self.llamadas: list[dict] = []

    def __call__(self, pdf_bytes, anio_esperado=None, client=None):
        self.llamadas.append({"pdf_bytes": pdf_bytes, "anio_esperado": anio_esperado,
                              "client": client})
        if self.error is not None:
            raise self.error
        return self.laboral


def _subir(cliente, caso_id: str, extractor: ExtractorFalso, monkeypatch):
    monkeypatch.setattr(api_main, "extraer_220", extractor)
    return cliente.post(f"/casos/{caso_id}/documentos/220",
                        files={"archivo": ("220.pdf", PDF, "application/pdf")})


# --- CRUD ---

def test_crear_y_leer_caso(cliente):
    caso_id = _crear(cliente)
    r = cliente.get(f"/casos/{caso_id}")
    assert r.status_code == 200
    assert r.json()["contribuyente"]["nombre"] == "G1 Asalariado"


def test_reemplazar_caso_persiste_el_nuevo_contenido(cliente):
    caso_id = _crear(cliente)
    caso = g1()
    caso.contribuyente.nombre = "Nombre Corregido A Mano"
    r = cliente.put(f"/casos/{caso_id}", json=caso.model_dump())
    assert r.status_code == 200
    assert r.json()["contribuyente"]["nombre"] == "Nombre Corregido A Mano"
    assert (cliente.get(f"/casos/{caso_id}").json()["contribuyente"]["nombre"]
            == "Nombre Corregido A Mano")


def test_body_con_clave_desconocida_es_422(cliente):
    # `extra="forbid"` en el caso: un typo del front no se descarta en silencio.
    r = cliente.post("/casos", json={**g1().model_dump(), "salariosss": 1})
    assert r.status_code == 422


@pytest.mark.parametrize("metodo, sufijo, cuerpo", [
    ("get", "", "vacio"),
    ("put", "", "caso"),
    ("post", "/liquidar", "vacio"),
    ("get", "/borrador", "vacio"),
    ("get", "/memoria", "vacio"),
    ("post", "/documentos/220", "archivo"),
])
def test_caso_inexistente_404(cliente, metodo, sufijo, cuerpo):
    # Todo endpoint pasa por el mismo guard: ninguno responde 200 sobre un caso ajeno.
    kwargs = {"caso": {"json": g1().model_dump()},
              "archivo": {"files": {"archivo": ("220.pdf", PDF, "application/pdf")}},
              "vacio": {}}[cuerpo]
    r = getattr(cliente, metodo)(f"/casos/no-existe{sufijo}", **kwargs)
    assert r.status_code == 404


# --- liquidación y render ---

def test_liquidar_devuelve_casillas_y_optimiza(cliente):
    caso_id = _crear(cliente)
    r = cliente.post(f"/casos/{caso_id}/liquidar")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["impuesto_neto"] == 1_495_977
    assert cuerpo["saldo"] == -6_504_023
    assert cuerpo["elecciones"] == {"usar_387": False, "usar_72uvt": True}
    assert cuerpo["combos_evaluados"] == 4
    assert any(c["codigo"] == "RLG_GENERAL" for c in cuerpo["casillas"])
    assert isinstance(cuerpo["flags"], list)


def test_liquidar_anio_sin_parametros_es_422(cliente):
    caso = g1()
    caso.anio_gravable = 1999
    caso_id = cliente.post("/casos", json=caso.model_dump()).json()["id"]
    r = cliente.post(f"/casos/{caso_id}/liquidar")
    # Año sin tabla es un dato del caso, no una falla del servidor.
    assert r.status_code == 422
    assert "1999" in r.json()["detail"]


def test_borrador_html(cliente):
    caso_id = _crear(cliente)
    r = cliente.get(f"/casos/{caso_id}/borrador")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "IMPUESTO_NETO" in r.text


def test_memoria_markdown_en_texto_plano(cliente):
    caso_id = _crear(cliente)
    r = cliente.get(f"/casos/{caso_id}/memoria")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert r.text.startswith("# Memoria de cálculo")
    assert "## IMPUESTO_NETO" in r.text


# --- upload 220 ---

def test_subir_220_agrega_hecho(cliente, monkeypatch):
    caso_id = _crear(cliente)
    r = _subir(cliente, caso_id, ExtractorFalso(), monkeypatch)
    assert r.status_code == 200
    assert len(r.json()["caso"]["laborales"]) == 2
    # y queda persistido, no solo en la respuesta
    assert len(cliente.get(f"/casos/{caso_id}").json()["laborales"]) == 2


def test_subir_220_pasa_el_anio_gravable_del_caso(cliente, monkeypatch):
    caso_id = _crear(cliente)
    extractor = ExtractorFalso()
    _subir(cliente, caso_id, extractor, monkeypatch)
    # El error más común es subir el 220 de otro año: el caso sabe cuál espera.
    assert extractor.llamadas[0]["anio_esperado"] == g1().anio_gravable
    assert extractor.llamadas[0]["pdf_bytes"] == PDF


@pytest.mark.parametrize("mensaje", [
    "la extracción no reconcilia contra el total impreso del certificado",
    "El certificado es del año gravable 2024 y se esperaba 2025",
    "El 220 reporta pensiones (30,000,000); regístralas como IngresoPension",
    "El archivo no parece un PDF (no empieza con %PDF)",
])
def test_error_del_documento_es_422_con_el_mensaje(cliente, monkeypatch, mensaje):
    # Documento malo = error del cliente, no del servidor: 422 y el mensaje llega tal cual.
    caso_id = _crear(cliente)
    r = _subir(cliente, caso_id, ExtractorFalso(error=ValueError(mensaje)), monkeypatch)
    assert r.status_code == 422
    assert r.json()["detail"] == mensaje


def test_error_del_documento_no_modifica_el_caso(cliente, monkeypatch):
    caso_id = _crear(cliente)
    _subir(cliente, caso_id, ExtractorFalso(error=ValueError("no reconcilia")), monkeypatch)
    assert len(cliente.get(f"/casos/{caso_id}").json()["laborales"]) == 1


@pytest.mark.parametrize("confianza, advierte", [
    (0.9, False), (0.7, False), (0.69, True), (0.55, True), (None, False),
])
def test_advertencia_por_confianza_baja(cliente, monkeypatch, confianza, advierte):
    # Borde exacto: 0.7 pasa, 0.69 advierte. Sin confianza declarada no se inventa alarma.
    caso_id = _crear(cliente)
    r = _subir(cliente, caso_id, ExtractorFalso(_laboral(confianza=confianza)), monkeypatch)
    assert r.status_code == 200
    advertencia = r.json()["advertencia"]
    assert (advertencia is not None) is advierte
    if advierte:
        assert str(confianza) in advertencia
    # El hecho se guarda igual: la advertencia es para revisar, no para bloquear.
    assert len(r.json()["caso"]["laborales"]) == 2


def test_guarda_el_pdf_para_que_la_fuente_sea_resoluble(cliente, monkeypatch, tmp_path):
    caso_id = _crear(cliente)
    r = _subir(cliente, caso_id, ExtractorFalso(_laboral(ref="deadbeef1234")), monkeypatch)
    ref = r.json()["caso"]["laborales"][1]["fuente"]["ref"]
    assert ref == "deadbeef1234"
    # `Fuente.ref` sin el documento detrás no es trazabilidad, es un string.
    assert (tmp_path / "documentos" / f"{ref}.pdf").read_bytes() == PDF


# --- almacén ---

def test_rutas_por_defecto_sin_variable_de_entorno(monkeypatch):
    monkeypatch.delenv("DECLARAS_DATOS", raising=False)
    assert almacen.ruta_caso("abc123").as_posix() == "var/casos/abc123.json"
    assert almacen.ruta_documento("abc123").as_posix() == "var/documentos/abc123.pdf"


def test_cargar_caso_inexistente_es_keyerror(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    with pytest.raises(KeyError):
        almacen.cargar("0123456789ab")


@pytest.mark.parametrize("id_malo", ["../../etc/passwd", "..", "a/b", "ABC", "x" * 65, ""])
def test_id_fuera_del_alfabeto_no_existe(tmp_path, monkeypatch, id_malo):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    with pytest.raises(KeyError):
        almacen.cargar(id_malo)


def test_id_con_traversal_no_alcanza_archivos_fuera_del_almacen(tmp_path, monkeypatch):
    # Sin alfabeto cerrado, `cargar("../otro")` resuelve a un archivo de AFUERA del
    # almacén (y `reemplazar` lo sobrescribe): el id viene del path del request.
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    vecino = tmp_path / "otro.json"
    vecino.write_text(g1().model_dump_json(), encoding="utf-8")

    with pytest.raises(KeyError):
        almacen.cargar("../otro")
    with pytest.raises(KeyError):
        almacen.reemplazar("../otro", g1().model_copy(update={"anio_gravable": 2024}))
    assert json.loads(vecino.read_text(encoding="utf-8"))["anio_gravable"] == 2025


def test_cargar_json_con_schema_viejo_es_error_de_dominio(tmp_path, monkeypatch):
    # Un caso persistido por una versión anterior + `extra="forbid"` = ValidationError.
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    caso_id = almacen.guardar(g1())
    ruta = almacen.ruta_caso(caso_id)
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["campo_de_una_version_vieja"] = 1
    ruta.write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        almacen.cargar(caso_id)
    assert "no se pudo leer" in str(exc.value)
    assert caso_id in str(exc.value)
    # NO es "no existe": un caso corrupto que responde 404 se ve como dato borrado.
    assert not isinstance(exc.value, KeyError)


def test_cargar_json_invalido_es_error_de_dominio(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    caso_id = almacen.guardar(g1())
    almacen.ruta_caso(caso_id).write_text('{"anio_gravable": ', encoding="utf-8")
    with pytest.raises(ValueError, match="no se pudo leer"):
        almacen.cargar(caso_id)


def test_caso_corrupto_en_disco_es_422_no_500(cliente, tmp_path):
    caso_id = _crear(cliente)
    ruta = tmp_path / "casos" / f"{caso_id}.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["laborales"][0]["campo_que_ya_no_existe"] = 1
    ruta.write_text(json.dumps(datos), encoding="utf-8")

    r = cliente.get(f"/casos/{caso_id}")
    assert r.status_code == 422
    assert "no se pudo leer" in r.json()["detail"]


def test_guardar_devuelve_ids_distintos(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    assert almacen.guardar(g1()) != almacen.guardar(g1())


def test_ida_y_vuelta_conserva_el_caso_completo(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    caso = g1()
    assert almacen.cargar(almacen.guardar(caso)) == caso
