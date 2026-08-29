"""La consulta publica de "¿me toca declarar?", que es la puerta del embudo.

Antes toda esa gente escribia por WhatsApp preguntando lo mismo y no quedaba rastro de
ninguna. Estas pruebas fijan lo que hace que valga la pena tenerla: que guarde el contacto,
que el veredicto lo decida el SERVIDOR, y que la clave de la DIAN nunca toque la base en claro.
"""

import pytest

from declaras.services.consultas_service import veredicto_de

BASE = "/v1/consultas"
CONTACTO = {"nombre": "Juan Valencia", "correo": "juan@correo.com", "whatsapp": "3175778348"}
TOPES = ("ingresos", "patrimonio", "consumo_tarjeta", "movimientos", "compras")


@pytest.mark.parametrize(
    ("respuestas", "esperado"),
    [
        ({"ingresos": "si"}, "OBLIGADO"),
        # Basta uno: el quinto tope tambien obliga aunque los cuatro anteriores sean "no".
        ({**{t: "no" for t in TOPES}, "compras": "si"}, "OBLIGADO"),
        ({t: "no" for t in TOPES}, "NO_OBLIGADO"),
        # UNA DUDA NO ES UN "NO": decir "no declares" sobre una pregunta sin contestar es
        # mandar a alguien a una sancion.
        ({**{t: "no" for t in TOPES}, "movimientos": "no-se"}, "NO_CONCLUYENTE"),
        # Incompleto tampoco concluye.
        ({"ingresos": "no"}, "NO_CONCLUYENTE"),
    ],
)
def test_la_regla_del_articulo_592(respuestas, esperado):
    assert veredicto_de(respuestas) == esperado


async def test_registra_la_consulta_y_devuelve_el_veredicto(client_sin_sesion):
    """Sin sesion a proposito: quien pregunta si le toca declarar todavia no es cliente."""
    respuesta = await client_sin_sesion.post(
        BASE, json={**CONTACTO, "via": "preguntas", "respuestas": {"ingresos": "si"}}
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["resultado"] == "OBLIGADO"
    assert cuerpo["consulta_id"]


async def test_el_veredicto_no_se_recibe_del_navegador(client_sin_sesion):
    """Mandar un veredicto cocinado no lo cambia: el servidor lo calcula de las respuestas.

    Sin esto bastaria abrir las herramientas del navegador para guardarse un "no obligado"
    que nadie calculo, y la regla del art. 592 viviria en dos sitios.
    """
    respuesta = await client_sin_sesion.post(
        BASE,
        json={
            **CONTACTO,
            "via": "preguntas",
            "respuestas": {"ingresos": "si"},
            "resultado": "NO_OBLIGADO",
        },
    )
    # `extra="forbid"`: el campo ni siquiera se acepta.
    assert respuesta.status_code == 422


async def test_una_respuesta_que_no_existe_se_rechaza(client_sin_sesion):
    respuesta = await client_sin_sesion.post(
        BASE, json={**CONTACTO, "via": "preguntas", "respuestas": {"ingresos": "quizas"}}
    )
    assert respuesta.status_code == 422
    assert "quizas" in respuesta.text or "ingresos" in respuesta.text


async def test_la_clave_de_la_dian_no_queda_en_claro(client_sin_sesion, container):
    """La regla cambio (ahora se guarda) pero NO cambio que se guarde cifrada."""
    clave = "MiClaveSuperSecreta2026"
    respuesta = await client_sin_sesion.post(
        BASE,
        json={**CONTACTO, "via": "dian", "id_number": "1007378576", "dian_password": clave},
    )
    assert respuesta.status_code == 200, respuesta.text

    from sqlalchemy import select

    from declaras.adapters.persistence.tables import ConsultaRow

    async with container.engine.begin() as conn:
        filas = (await conn.execute(select(ConsultaRow.dian_password_cifrada))).scalars().all()
    guardadas = [f for f in filas if f]
    assert guardadas, "la clave tiene que quedar guardada"
    assert all(clave not in f for f in guardadas), "quedo en claro en la base"

    # Y se puede recuperar, que es el requisito que obliga a cifrar en vez de hashear.
    from declaras.services.cifrado import descifrar

    assert descifrar(guardadas[0], llave=container.settings.clave_de_cifrado) == clave


async def test_la_consulta_con_la_dian_devuelve_los_cinco_topes(client_sin_sesion):
    """La respuesta EXACTA, contra lo que la DIAN ya tiene reportado.

    Es lo que distingue este camino del cuestionario: el cuestionario compara contra lo que la
    persona RECUERDA, y quien recuerda por debajo se entera de su error con la sancion. Aca se
    compara contra las cifras con las que la DIAN decide, asi que el resultado puede decir
    cuanto y por que, no solo si.
    """
    respuesta = await client_sin_sesion.post(
        f"{BASE}/dian",
        json={
            **CONTACTO,
            "id_number": "1007378576",
            "dian_password": "clave-buena",
            "tax_year": 2025,
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["resultado"] in {"OBLIGADO", "NO_OBLIGADO"}
    # Los CINCO topes, no solo el que obliga: sin los otros, la persona no sabe que tan cerca
    # estuvo de los demas.
    assert {t["codigo"] for t in cuerpo["topes"]} == set(TOPES)
    for tope in cuerpo["topes"]:
        assert tope["limite"] > 0
        assert isinstance(tope["supera"], bool)
        assert tope["nombre"]


async def test_una_clave_mala_no_deja_un_registro_a_medias(client_sin_sesion, container):
    """Si el portal rechaza la clave, no se guarda la consulta: seria un lead con un dato falso."""
    respuesta = await client_sin_sesion.post(
        f"{BASE}/dian",
        json={
            **CONTACTO,
            "id_number": "1007378576",
            "dian_password": "clave-bad",
            "tax_year": 2025,
        },
    )
    assert respuesta.status_code in (401, 422)

    from sqlalchemy import func, select

    from declaras.adapters.persistence.tables import ConsultaRow

    async with container.engine.begin() as conn:
        n = (await conn.execute(select(func.count()).select_from(ConsultaRow))).scalar()
    assert n == 0
