from jinja2 import Template

from declaras.caso import CasoTributario
from declaras.motor import Liquidacion
from declaras.render.memoria import casillas

# autoescape: el nombre del contribuyente llega de extracción LLM y del API — dato no
# confiable que se interpola en el <title> y el <p>. Las fórmulas (−, ×, Σ) no se tocan.
_PLANTILLA = Template("""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Borrador 210 — {{ caso.contribuyente.nombre }}</title>
<style>
body{font-family:Georgia,serif;max-width:900px;margin:2rem auto;color:#1a1a1a}
h1{font-size:1.4rem} table{border-collapse:collapse;width:100%}
td,th{border:1px solid #bbb;padding:.45rem .6rem;font-size:.9rem;text-align:left}
td.v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.neg td.v{color:#0a6e0a} .flag{background:#fff3cd;border:1px solid #e0c000;
padding:.5rem .8rem;margin:.4rem 0;font-size:.9rem}
small{color:#555} @media print{.flag{break-inside:avoid}}
</style></head><body>
<h1>Borrador Formulario 210 — año gravable {{ liq.anio_gravable }}</h1>
<p>{{ caso.contribuyente.nombre }} · {{ caso.contribuyente.tipo_doc }}
{{ caso.contribuyente.num_doc }}<br>
<small>BORRADOR por conceptos — el mapeo a casillas oficiales DIAN está pendiente.
Elecciones: art. 387 = {{ "sí" if liq.elecciones.usar_387 else "no" }},
72 UVT = {{ "sí" if liq.elecciones.usar_72uvt else "no" }}.</small></p>
{% for f in liq.flags %}<div class="flag"><b>{{ f.codigo }}</b> — {{ f.mensaje }}</div>{% endfor %}
<table><tr><th>Concepto</th><th>Valor</th><th>Cómo se calculó</th><th>Norma</th></tr>
{% for c in filas %}<tr{% if c.valor < 0 %} class="neg"{% endif %}>
<td><b>{{ c.etiqueta }}</b><br><small>{{ c.codigo }}</small></td>
<td class="v">{{ "{:,}".format(c.valor) }}</td>
<td><small>{{ c.formula }}</small>
{%- if c.insumos %}<br><small>Insumos: {{ c.insumos|join(", ") }}</small>{% endif %}</td>
<td><small>{{ c.regla or "" }}</small></td>
</tr>{% endfor %}</table>
</body></html>""", autoescape=True)


def borrador_html(liq: Liquidacion, caso: CasoTributario) -> str:
    return _PLANTILLA.render(liq=liq, caso=caso, filas=casillas(liq))
