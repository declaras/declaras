from declaras.caso import CasoTributario
from declaras.dinero import pesos, porcentaje
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio

# Tope del art. 258 ET: los descuentos del art. 257 no pueden exceder el 25% del
# impuesto a cargo. Constante legal, no parámetro anual.
_TOPE_258_PCT = 0.25


def cerrar(caso: CasoTributario, p: ParametrosAnio, t: Traza, impuesto_neto: int) -> None:
    retenciones = t.nodo(
        "RETENCIONES", "Total retenciones en la fuente",
        sum(l.retencion for l in caso.laborales)
        + sum(pn.retencion for pn in caso.pensiones)
        + sum(r.retencion for r in caso.rendimientos)
        + sum(a.retencion for a in caso.arriendos)
        + sum(d.retencion for d in caso.dividendos),
        "suma de retenciones de todas las fuentes",
    )

    anios = caso.creditos.anios_previos_declarando
    pct = p.anticipo_pct[min(anios, len(p.anticipo_pct) - 1)]
    base = impuesto_neto
    detalle = f"impuesto del año {impuesto_neto:,}"
    anterior = caso.creditos.impuesto_neto_anio_anterior
    if anios >= 1 and anterior is not None:
        promedio = pesos((impuesto_neto + anterior) / 2)
        if promedio < base:
            base, detalle = promedio, f"promedio dos años {promedio:,} (menor)"
    anticipo = t.nodo(
        "ANTICIPO_SIGUIENTE", "Anticipo del año siguiente",
        max(0, porcentaje(base, pct) - retenciones),
        f"max(0, {pct:.0%} × {detalle} − retenciones {retenciones:,})",
        insumos=["IMPUESTO_NETO", "RETENCIONES"], regla="art. 807 ET",
    )

    # Los dos últimos términos no son nodos del 210 (vienen de creditos), así que la
    # fórmula los interpola con su valor: sin eso el saldo no se puede recomponer.
    pagado = caso.creditos.anticipo_pagado
    favor_anterior = caso.creditos.saldo_favor_anterior
    t.nodo(
        "SALDO", "Saldo a pagar (+) o a favor (−)",
        impuesto_neto + anticipo - retenciones - pagado - favor_anterior,
        f"impuesto neto {impuesto_neto:,} + anticipo siguiente {anticipo:,} "
        f"− retenciones {retenciones:,} − anticipo pagado {pagado:,} "
        f"− saldo a favor anterior {favor_anterior:,}",
        insumos=["IMPUESTO_NETO", "ANTICIPO_SIGUIENTE", "RETENCIONES"],
    )


def validar(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> None:
    bruto_pat = t.nodo("PATRIMONIO_BRUTO", "Patrimonio bruto a 31 dic",
                       sum(a.valor_31dic for a in caso.patrimonio.activos),
                       "suma de activos")
    liquido = t.nodo("PATRIMONIO_LIQUIDO", "Patrimonio líquido a 31 dic",
                     bruto_pat - sum(d.saldo_31dic for d in caso.patrimonio.deudas),
                     "PATRIMONIO_BRUTO − deudas", insumos=["PATRIMONIO_BRUTO"])

    criterios = []
    if caso.ingresos_brutos_totales >= p.uvt_pesos(p.tope_obligacion_ingresos_uvt):
        criterios.append("ingresos ≥ 1.400 UVT")
    if bruto_pat > p.uvt_pesos(p.tope_obligacion_patrimonio_uvt):
        criterios.append("patrimonio > 4.500 UVT")
    mov = caso.movimientos
    if mov.consignaciones_totales and mov.consignaciones_totales.valor \
            > p.uvt_pesos(p.tope_obligacion_consignaciones_uvt):
        criterios.append("consignaciones > 1.400 UVT")
    if mov.compras_y_consumos and mov.compras_y_consumos.valor \
            > p.uvt_pesos(p.tope_obligacion_consignaciones_uvt):
        criterios.append("compras y consumos > 1.400 UVT")
    t.nodo("OBLIGADO_DECLARAR", "¿Obligado a declarar?",
           1 if criterios else 0,
           "; ".join(criterios) or "ningún criterio superado",
           regla="art. 592-594-3 ET")
    if not criterios:
        t.flag("NO_OBLIGADO", "No supera ningún tope de obligación: la declaración "
               "sería voluntaria.", severidad="info")

    anterior = caso.patrimonio.patrimonio_liquido_anterior
    if anterior is not None:
        incremento = liquido - anterior
        # Art. 236 ET: las rentas exentas también justifican el incremento, así que
        # se usa el ingreso pensional TOTAL (gravado + exento), no RLG_PENSIONES.
        pension_total = sum(sum(pn.mesadas) for pn in caso.pensiones)
        justificado = (
            t.nodos["RLG_GENERAL"].valor + pension_total
            + t.nodos["DIV_NO_GRAVADOS"].valor + t.nodos["DIV_GRAVADOS"].valor
            + t.nodos["APLICADO_40"].valor + t.nodos["EXTRA_LIMITE"].valor
            + t.nodos["INCR_TOTAL"].valor
        )
        gastado = (t.nodos["RETENCIONES"].valor + caso.creditos.anticipo_pagado)
        if justificado < incremento + gastado:
            t.flag(
                "COMPARACION_PATRIMONIAL",
                f"El patrimonio líquido creció {incremento:,} pero las rentas del año "
                f"solo justifican {justificado:,} (aprox.). Documentar el origen "
                "(herencia, donación, préstamo, venta) antes de presentar.",
            )

    # --- Chequeos de sanidad: solo flags, nunca alteran cifras ---

    if caso.contribuyente.residente is False:
        t.flag("NO_RESIDENTE",
               "El motor solo implementa el régimen de residentes; "
               "este 210 no es válido para no residentes.")

    bruto_laboral = sum(l.bruto for l in caso.laborales)
    incr_aportes = t.nodos["INCR_APORTES"].valor
    if incr_aportes > bruto_laboral:
        t.flag("APORTES_EXCEDEN_BRUTO",
               f"Los aportes obligatorios ({incr_aportes:,}) superan el ingreso bruto "
               f"laboral ({bruto_laboral:,}): la traza aguas arriba es absurda; "
               "revisar la captura de los certificados 220.")

    fuentes_retencion = (
        [(f"laboral {l.empleador_nombre}", l.retencion, l.bruto)
         for l in caso.laborales]
        + [(f"pensión {pn.pagador}", pn.retencion, sum(pn.mesadas))
           for pn in caso.pensiones]
        + [(f"rendimientos {r.entidad}", r.retencion, r.valor)
           for r in caso.rendimientos]
        + [(f"arriendo {a.inmueble}", a.retencion, a.canon_total)
           for a in caso.arriendos]
        + [(f"dividendos {d.sociedad_nombre}", d.retencion,
            d.no_gravados + d.gravados) for d in caso.dividendos]
    )
    for nombre, retencion, base in fuentes_retencion:
        if retencion > base:
            t.flag("RETENCION_EXCEDE_INGRESO",
                   f"La retención de {nombre} ({retencion:,}) supera su base "
                   f"({base:,}): una retención inflada fabrica un saldo a favor falso.")

    impuesto_cargo = t.nodos["IMPUESTO_241"].valor + t.nodos["IMP_DIV_35"].valor
    tope_258 = porcentaje(impuesto_cargo, _TOPE_258_PCT)
    if t.nodos["DESCUENTO_DONACIONES"].valor > tope_258:
        t.flag("TOPE_DESCUENTO_DONACIONES",
               f"El descuento por donaciones "
               f"({t.nodos['DESCUENTO_DONACIONES'].valor:,}) excede el tope del "
               f"art. 258 (25% del impuesto = {tope_258:,}); el exceso no es "
               "descontable este año.")
