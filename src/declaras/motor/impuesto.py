from declaras.caso import CasoTributario
from declaras.dinero import pesos
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio
from declaras.parametros.tabla import impuesto_tabla_241


def impuesto_total(caso: CasoTributario, p: ParametrosAnio, t: Traza,
                   rlg_general: int, rlg_pensiones: int) -> int:
    """Cédula de dividendos + tabla 241 + descuentos → impuesto neto."""
    no_grav = t.nodo("DIV_NO_GRAVADOS", "Dividendos no gravados (art. 49)",
                     sum(d.no_gravados for d in caso.dividendos),
                     "suma certificados", regla="art. 242 ET")
    grav = t.nodo("DIV_GRAVADOS", "Dividendos gravados",
                  sum(d.gravados for d in caso.dividendos),
                  "suma certificados", regla="art. 240 ET")

    imp_35 = t.nodo("IMP_DIV_35", "Impuesto 35% sobre dividendos gravados",
                    pesos(grav * p.dividendos_tarifa_gravados),
                    f"35% × {grav:,}", insumos=["DIV_GRAVADOS"],
                    regla="art. 242 par. / art. 240 ET")
    neto_grav = grav - imp_35

    base = t.nodo("BASE_TABLA_241", "Base gravable tabla art. 241",
                  rlg_general + rlg_pensiones + no_grav + neto_grav,
                  f"RLG_GENERAL {rlg_general:,} + RLG_PENSIONES {rlg_pensiones:,} "
                  f"+ no gravados {no_grav:,} + neto gravados {neto_grav:,}",
                  insumos=["RLG_GENERAL", "RLG_PENSIONES", "DIV_NO_GRAVADOS", "DIV_GRAVADOS"])

    imp_241 = t.nodo("IMPUESTO_241", "Impuesto tabla art. 241",
                     impuesto_tabla_241(base, p), "tabla marginal art. 241",
                     insumos=["BASE_TABLA_241"], regla="art. 241 ET")

    base_desc = max(0, (no_grav + neto_grav)
                    - p.uvt_pesos(p.descuento_dividendos_umbral_uvt))
    desc_div = t.nodo("DESCUENTO_254_1", "Descuento marginal por dividendos",
                      pesos(base_desc * p.descuento_dividendos_pct),
                      f"19% × max(0, dividendos en base − 1.090 UVT) = 19% × {base_desc:,}",
                      regla="art. 254-1 ET")

    donado = sum(d.valor for d in caso.beneficios.donaciones_esal if d.certificada)
    desc_don = t.nodo("DESCUENTO_DONACIONES", "Descuento donaciones ESAL certificadas",
                      pesos(donado * p.donaciones_descuento_pct),
                      f"25% × {donado:,}", regla="art. 257 ET")

    return t.nodo("IMPUESTO_NETO", "Impuesto neto de renta",
                  max(0, imp_241 + imp_35 - desc_div - desc_don),
                  f"{imp_241:,} + {imp_35:,} − {desc_div:,} − {desc_don:,} (piso 0)",
                  insumos=["IMPUESTO_241", "IMP_DIV_35",
                           "DESCUENTO_254_1", "DESCUENTO_DONACIONES"])
