"""
Vaxta Edge — Página de Court Metrics (complemento temporal)

Court Metrics no tiene API ni export por club (ver notas en
VAXTA_EDGE_SUPABASE_CONTEXTO.md) — todo dato viene de una lectura manual
de su plataforma, guardada en courtmetrics_reves.json. Esta página solo
lee ese archivo y dibuja las gráficas en SVG con esos números fijos; no
consulta Supabase ni ningún servicio en vivo. Para refrescar el dato hay
que volver a capturar manualmente y actualizar el JSON.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

TEAL = "#1A8A8A"
RED = "#C0392B"
MUTED = "#7a7670"
GRID = "#e5e2da"


def _fmt_eje(v, unidad):
    """Símbolos de moneda van antes del número ($36,320); el resto (%, h, mm, °) va después."""
    if unidad in ("€", "$", "£"):
        return f"{unidad}{v:,.0f}"
    return f"{v:,.0f}{unidad}"


def _fmt_moneda(valor, moneda):
    simbolo = {"EUR": "€", "USD": "$", "GBP": "£", "MXN": "$"}.get(moneda, moneda + " ")
    return f"{simbolo}{valor:,.0f}"


def _delta_html(delta, delta_pct=None, delta_pp=None):
    if delta is None:
        return '<span class="flat">— sin periodo anterior</span>'
    cls = "flat" if delta == 0 else ("up" if delta > 0 else "down")
    arrow = "→" if delta == 0 else ("↑" if delta > 0 else "↓")
    if delta_pp is not None:
        detalle = f"{delta_pp:+d}pp ({delta_pct:+d}%)"
    elif delta_pct is not None:
        detalle = f"{delta:+,.0f} ({delta_pct:+d}%)"
    else:
        detalle = f"{delta:+,.0f}"
    return f'<span class="{cls}">{arrow} {detalle}</span> vs. periodo anterior'


def _tile_class(delta):
    if not delta:
        return ""
    return "up" if delta > 0 else "down"


def _dia_corto(fecha_iso):
    meses = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    y, m, d = fecha_iso.split("-")
    return f"{int(d)} {meses[int(m)]}"


def _svg_linea_doble(fechas, serie_a, serie_b, ref_val=None, ref_label="", unidad="", w=680, h=200):
    """Línea del periodo actual (teal, sólida) vs. periodo anterior (gris, punteada),
    más una línea de referencia horizontal opcional (benchmark de zona)."""
    pad_l, pad_r, pad_t, pad_b = 42, 10, 16, 22
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    todos = serie_a + serie_b + ([ref_val] if ref_val else [])
    vmax = max(todos) * 1.08
    vmin = 0
    n = len(fechas)

    def xy(i, v):
        x = pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l
        y = pad_t + plot_h - ((v - vmin) / (vmax - vmin)) * plot_h
        return x, y

    def polyline(serie):
        pts = " ".join(f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, v in enumerate(serie))
        return pts

    grid_lines = ""
    n_grid = 4
    for g in range(n_grid + 1):
        gy = pad_t + plot_h - (g / n_grid) * plot_h
        gv = vmin + (g / n_grid) * (vmax - vmin)
        etiqueta = _fmt_eje(gv, unidad)
        grid_lines += (
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - pad_r}" y2="{gy:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{gy + 3:.1f}" text-anchor="end" class="axis-lbl">{etiqueta}</text>'
        )

    x_labels = ""
    for i in range(0, n, 6):
        x, _ = xy(i, 0)
        x_labels += f'<text x="{x:.1f}" y="{h - 4}" text-anchor="middle" class="axis-lbl">{_dia_corto(fechas[i])}</text>'

    ref_line = ""
    if ref_val is not None:
        _, ry = xy(0, ref_val)
        ref_line = (
            f'<line x1="{pad_l}" y1="{ry:.1f}" x2="{w - pad_r}" y2="{ry:.1f}" '
            f'stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="3,3"/>'
            f'<text x="{w - pad_r}" y="{ry - 4:.1f}" text-anchor="end" class="axis-lbl" fill="{MUTED}">{ref_label}</text>'
        )

    return f'''<svg viewBox="0 0 {w} {h}">
      {grid_lines}
      <polyline points="{polyline(serie_b)}" fill="none" stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="4,3"/>
      <polyline points="{polyline(serie_a)}" fill="none" stroke="{TEAL}" stroke-width="2.2"/>
      {ref_line}
      {x_labels}
    </svg>'''


def _svg_barras(labels, values, unidad="", w=680, h=190, color=TEAL, highlight_idx=None):
    pad_l, pad_r, pad_t, pad_b = 38, 8, 16, 26
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(labels)
    vmax = max(values) * 1.15 if values else 1
    bar_w = plot_w / n * 0.62
    gap = plot_w / n

    bars = ""
    for i, v in enumerate(values):
        bx = pad_l + i * gap + (gap - bar_w) / 2
        bh = (v / vmax) * plot_h if vmax else 0
        by = pad_t + plot_h - bh
        c = RED if highlight_idx == i else color
        bars += f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{max(bh,1):.1f}" rx="2" fill="{c}"/>'

    step = max(1, n // 12)
    x_labels = ""
    for i in range(0, n, step):
        x = pad_l + i * gap + gap / 2
        x_labels += f'<text x="{x:.1f}" y="{h - 6}" text-anchor="middle" class="axis-lbl">{labels[i]}</text>'

    grid_lines = ""
    for g in range(5):
        gy = pad_t + plot_h - (g / 4) * plot_h
        gv = (g / 4) * vmax
        grid_lines += (
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - pad_r}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{gy + 3:.1f}" text-anchor="end" class="axis-lbl">{_fmt_eje(gv, unidad)}</text>'
        )

    return f'<svg viewBox="0 0 {w} {h}">{grid_lines}{bars}{x_labels}</svg>'


def _svg_rango_horas(horas, mins, maxs, avgs, unidad="€", w=680, h=190):
    pad_l, pad_r, pad_t, pad_b = 38, 8, 16, 26
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(horas)
    vmax = max(maxs) * 1.1
    gap = plot_w / n
    bar_w = gap * 0.5

    bars = ""
    for i in range(n):
        bx = pad_l + i * gap + (gap - bar_w) / 2
        y_min = pad_t + plot_h - (mins[i] / vmax) * plot_h
        y_max = pad_t + plot_h - (maxs[i] / vmax) * plot_h
        y_avg = pad_t + plot_h - (avgs[i] / vmax) * plot_h
        bars += (
            f'<rect x="{bx:.1f}" y="{y_max:.1f}" width="{bar_w:.1f}" height="{max(y_min-y_max,1):.1f}" '
            f'rx="2" fill="{TEAL}" opacity="0.35"/>'
            f'<line x1="{bx:.1f}" y1="{y_avg:.1f}" x2="{bx+bar_w:.1f}" y2="{y_avg:.1f}" stroke="{TEAL}" stroke-width="2.2"/>'
        )

    x_labels = "".join(
        f'<text x="{pad_l + i*gap + gap/2:.1f}" y="{h-6}" text-anchor="middle" class="axis-lbl">{horas[i]}</text>'
        for i in range(0, n, 2)
    )
    grid_lines = ""
    for g in range(5):
        gy = pad_t + plot_h - (g / 4) * plot_h
        gv = (g / 4) * vmax
        grid_lines += (
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w-pad_r}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>'
            f'<text x="{pad_l-6}" y="{gy+3:.1f}" text-anchor="end" class="axis-lbl">{_fmt_eje(gv, unidad)}</text>'
        )
    return f'<svg viewBox="0 0 {w} {h}">{grid_lines}{bars}{x_labels}</svg>'


def _svg_ranking(items, key, label_fmt, unidad="", w=680, highlight_key="es_reves"):
    """Barras horizontales ranqueadas, club propio resaltado en rojo."""
    filtrados = [c for c in items if c.get(key) is not None]
    ordenado = sorted(filtrados, key=lambda c: c[key], reverse=True)
    vmax = max(c[key] for c in ordenado) if ordenado else 1
    row_h = 24
    h = len(ordenado) * row_h + 10
    pad_l = 190
    plot_w = w - pad_l - 70

    rows = ""
    for i, c in enumerate(ordenado):
        y = 6 + i * row_h
        bw = (c[key] / vmax) * plot_w if vmax else 0
        color = RED if c.get(highlight_key) else TEAL
        weight = "700" if c.get(highlight_key) else "400"
        rows += (
            f'<text x="{pad_l - 8}" y="{y + 14}" text-anchor="end" class="bar-lbl" '
            f'font-weight="{weight}" fill="{color if c.get(highlight_key) else "var(--text-secondary)"}">{c["nombre"]}</text>'
            f'<rect x="{pad_l}" y="{y + 3}" width="{max(bw,1):.1f}" height="16" rx="3" fill="{color}"/>'
            f'<text x="{pad_l + bw + 6:.1f}" y="{y + 14}" class="bar-val">{label_fmt(c[key])}</text>'
        )
    return f'<svg viewBox="0 0 {w} {h}" style="height:{h}px">{rows}</svg>'


def generar_html_courtmetrics():
    with open(os.path.join(HERE, "courtmetrics_reves.json"), encoding="utf-8") as f:
        data = json.load(f)

    club = data["club"]
    k = data["kpis"]
    moneda = data["moneda"]
    simbolo = {"EUR": "€", "USD": "$", "GBP": "£", "MXN": "$"}.get(moneda, moneda + " ")
    zona = data["zona_benchmark"]

    ingreso = k["ingreso_total"]
    por_cancha = k["ingreso_por_cancha"]
    ocupacion = k["ocupacion_pct"]
    reservadas = k["horas_reservadas"]
    disponibles = k["horas_disponibles"]

    tiles = [
        {"k": "Ingreso total", "v": _fmt_moneda(ingreso["valor"], moneda),
         "delta": _delta_html(ingreso["delta"], delta_pct=ingreso["delta_pct"]), "cls": _tile_class(ingreso["delta"])},
        {"k": "Ingreso por cancha", "v": _fmt_moneda(por_cancha["valor"], moneda),
         "delta": _delta_html(por_cancha["delta"], delta_pct=por_cancha["delta_pct"]), "cls": _tile_class(por_cancha["delta"])},
        {"k": "Ocupación promedio", "v": f"{ocupacion['valor']}%",
         "delta": _delta_html(ocupacion["delta_pp"], delta_pct=ocupacion["delta_pct"], delta_pp=ocupacion["delta_pp"]),
         "cls": _tile_class(ocupacion["delta_pp"])},
        {"k": "Horas reservadas", "v": f"{reservadas['valor']:,}",
         "delta": _delta_html(reservadas["delta"], delta_pct=reservadas["delta_pct"]), "cls": _tile_class(reservadas["delta"])},
        {"k": "Horas disponibles", "v": f"{disponibles['valor']:,}",
         "delta": _delta_html(disponibles["delta"], delta_pct=disponibles["delta_pct"]), "cls": _tile_class(disponibles["delta"])},
        {"k": "Rating de Google", "v": f"{club['rating_google']} ★",
         "delta": f'<span class="flat">{club["resenas_google"]} reseñas</span>', "cls": ""},
    ]
    tiles_html = "".join(
        f'<div class="stat-tile {t["cls"]}"><div class="k">{t["k"]}</div>'
        f'<div class="v">{t["v"]}</div><div class="delta">{t["delta"]}</div></div>'
        for t in tiles
    )

    # ---- Series diarias (ingreso, ocupación, horas reservadas) ----
    serie = data["serie_diaria"]
    fechas = serie["actual"]["fechas"]
    svg_ingreso_diario = _svg_linea_doble(
        fechas, serie["actual"]["ingreso_mxn"], serie["anterior"]["ingreso_mxn"],
        ref_val=zona["ingreso_prom_diario"], ref_label=zona["nombre"], unidad=simbolo
    )
    svg_ocupacion_diaria = _svg_linea_doble(
        fechas, serie["actual"]["ocupacion_pct"], serie["anterior"]["ocupacion_pct"],
        ref_val=zona["ocupacion_pct_prom_diario"], ref_label=zona["nombre"], unidad="%"
    )
    svg_horas_diarias = _svg_linea_doble(
        fechas, serie["actual"]["horas_reservadas"], serie["anterior"]["horas_reservadas"], unidad="h"
    )

    # ---- Ocupación y precio por hora del día ----
    oh = data["ocupacion_por_hora"]
    svg_ocup_hora = _svg_barras([r["hora"][:2] for r in oh], [r["pct"] for r in oh], unidad="%")

    ph = data["precio_por_hora"]
    svg_precio_hora = _svg_rango_horas(
        [r["hora"][:2] for r in ph], [r["min"] for r in ph], [r["max"] for r in ph], [r["avg"] for r in ph],
        unidad=simbolo
    )

    # ---- Anticipación de reserva ----
    ant = sorted(data["anticipacion_reserva"], key=lambda r: -r["dias_antes"])
    svg_anticipacion = _svg_barras(
        [f'{r["dias_antes"]}d' if r["dias_antes"] else "hoy" for r in ant],
        [round(r["ocupacion_normalizada"] * 100) for r in ant], unidad="%"
    )

    # ---- Clima ----
    clima = data["clima"]
    svg_temp = _svg_linea_doble(clima["fechas"], clima["temp_max_c"], clima["temp_min_c"], unidad="°")
    svg_precip = _svg_barras([_dia_corto(f) for f in clima["fechas"][::3]], clima["precipitacion_mm"][::3], unidad="mm")

    # ---- Competencia ----
    comp = data["competencia"]
    tabla_filas = "".join(
        f'''<tr{' style="font-weight:700;background:var(--series-1-wash);"' if c["es_reves"] else ""}>
          <td>{c["nombre"]}</td>
          <td style="text-align:right">{"—" if c["distancia_km"] == 0 else f'{c["distancia_km"]:.1f} km'}</td>
          <td style="text-align:right">{c["canchas"]}</td>
          <td style="text-align:right">{_fmt_moneda(c["ingreso_mxn"], moneda)}</td>
          <td style="text-align:right">{_fmt_moneda(c["ingreso_cancha_mxn"], moneda)}</td>
          <td style="text-align:right">{f'{c["ocupacion_pct"]}%' if c["ocupacion_pct"] is not None else "—"}</td>
          <td style="text-align:right">{f'{c["rating_google"]} ★' if c["rating_google"] is not None else "—"}</td>
        </tr>'''
        for c in sorted(comp, key=lambda c: c["distancia_km"])
    )

    fmt_moneda_fn = lambda v: _fmt_moneda(v, moneda)  # noqa: E731
    svg_rank_ingreso = _svg_ranking(comp, "ingreso_mxn", fmt_moneda_fn)
    svg_rank_ingreso_cancha = _svg_ranking(comp, "ingreso_cancha_mxn", fmt_moneda_fn)
    svg_rank_ocupacion = _svg_ranking(comp, "ocupacion_pct", lambda v: f"{v}%")
    svg_rank_precio_max = _svg_ranking(comp, "precio_max", lambda v: f"{simbolo}{v:.0f}")
    svg_rank_rating = _svg_ranking(comp, "rating_google", lambda v: f"{v} ★")

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vaxta Edge — Inteligencia de mercado</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --navy: #1E2D4A; --teal: #1A8A8A; --teal-light: #22a8a8; --sand: #E8E6E1;
  --surface-1: #ffffff; --page:#F5F3EE; --text-primary:#2D2D2D; --text-secondary:#5c5954;
  --text-muted:#7a7670; --border: #d4d0c8; --gridline:#e5e2da;
  --heading-ink: var(--navy);
  --warn-wash:#FEF3E2; --warn-ink:#D4820A;
  --good-ink:#1A7A4A; --crit-ink:#C0392B; --series-1-wash:#EAF5F5;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --navy:#141f36; --teal:#22a8a8; --teal-light:#3cc4c4; --sand:#2a2a26;
    --surface-1:#182238; --page:#0c1220; --text-primary:#f2f1ec; --text-secondary:#b8b5ac;
    --text-muted:#8b887f; --border: #2a3550; --heading-ink: var(--text-primary); --gridline:#26314a;
    --warn-wash:#2c2415; --warn-ink:#e0a53f;
    --good-ink:#3fc37e; --crit-ink:#e2685c; --series-1-wash:#152738;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: 'DM Sans', system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--text-primary); background: var(--page); margin: 0;
  padding: 0 0 40px; line-height: 1.5;
}}
.wrap {{ max-width: 760px; margin: 0 auto; padding: 0 20px; }}
.hdr {{ background: var(--navy); margin: 0 0 22px; padding: 26px 20px 22px;
  border-bottom: 3px solid var(--teal); }}
.hdr-inner {{ max-width: 760px; margin: 0 auto; display: flex; align-items: center; gap: 16px; }}
.hdr-logo {{ height: 34px; width: auto; flex-shrink: 0; }}
.hdr-text {{ flex: 1; min-width: 0; }}
.hdr .eyebrow {{ font-size: 11px; letter-spacing: 2.5px; text-transform: uppercase;
  color: var(--teal-light); margin: 0 0 6px; }}
.hdr h1 {{ font-family: 'Cormorant Garamond', serif; font-size: 26px; font-weight: 700;
  color: #fff; margin: 0 0 6px; line-height: 1.15; }}
.hdr p {{ font-size: 12.5px; color: rgba(255,255,255,0.55); margin: 0; }}
.aviso {{ background: var(--warn-wash); border: 1px solid var(--warn-ink); border-radius: 4px;
  padding: 12px 16px; font-size: 12.5px; color: var(--text-primary); margin-bottom: 18px; }}
.aviso strong {{ color: var(--warn-ink); }}
.card {{ background: var(--surface-1); border:1px solid var(--border); border-radius:4px; padding:18px 18px 14px; margin-bottom: 16px; position: relative; overflow: hidden; }}
.card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; background: var(--teal); }}
.card h2 {{ font-family: 'Cormorant Garamond', serif; font-size: 19px; font-weight: 700; color: var(--heading-ink); margin: 0 0 2px; }}
.card .sub {{ font-size: 11px; letter-spacing: 0.2px; color: var(--text-muted); margin: 0 0 10px; }}
.stats {{ display:grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }}
.stat-tile {{ background: var(--surface-1); border:1px solid var(--border); border-radius: 4px; padding: 14px 16px; position: relative; overflow: hidden; }}
.stat-tile::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; background: var(--teal); }}
.stat-tile.up::before {{ background: var(--good-ink); }}
.stat-tile.down::before {{ background: var(--crit-ink); }}
.stat-tile .k {{ font-size: 11px; letter-spacing: 0.6px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; }}
.stat-tile .v {{ font-family: 'Cormorant Garamond', serif; font-size: 28px; font-weight: 700; color: var(--heading-ink); font-variant-numeric: tabular-nums; }}
.stat-tile .delta {{ font-size: 11px; margin-top: 6px; font-variant-numeric: tabular-nums; }}
span.up {{ color: var(--good-ink); font-weight: 600; }}
span.down {{ color: var(--crit-ink); font-weight: 600; }}
span.flat {{ color: var(--text-muted); }}
@media (max-width: 640px) {{ .stats {{ grid-template-columns: repeat(2, 1fr); }} }}
.grid2 {{ display:grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
@media (max-width: 640px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
svg {{ width: 100%; height: auto; overflow: visible; }}
.axis-lbl {{ font-size: 9.5px; fill: var(--text-muted); }}
.bar-lbl {{ font-size: 11px; fill: var(--text-secondary); }}
.bar-val {{ font-size: 10.5px; fill: var(--text-muted); font-variant-numeric: tabular-nums; }}
.legend {{ display:flex; flex-wrap:wrap; gap: 10px 16px; margin-top: 10px; font-size: 11px; color: var(--text-secondary); }}
.legend .item {{ display:flex; align-items:center; gap:6px; }}
.legend .dot {{ width:9px; height:9px; border-radius:2px; flex-shrink:0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
th {{ text-align: left; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.4px; color: var(--text-muted);
  border-bottom: 1px solid var(--border); padding: 6px 8px; }}
th:not(:first-child), td:not(:first-child) {{ text-align: right; }}
td {{ padding: 7px 8px; border-bottom: 1px solid var(--gridline); }}
.tablewrap {{ overflow-x: auto; }}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-inner">
    <img class="hdr-logo" src="/logo_vaxta_edge.png" alt="Vaxta Edge" onerror="this.style.display='none'">
    <div class="hdr-text">
      <p class="eyebrow">Vaxta Edge · Inteligencia de Pádel</p>
      <h1>Inteligencia de mercado — {club['nombre']}</h1>
    </div>
  </div>
</div>
<div class="wrap">
  <div class="aviso">
    <strong>Captura manual, no en vivo.</strong> Estos datos vienen de una fuente externa de inteligencia
    de mercado, leídos a mano el {data['capturado_el']} — no se actualizan solos. Periodo:
    {data['periodo_actual']} vs. {data['periodo_anterior']} (ventana de {data['ventana_dias']} días, moneda {moneda}).
  </div>

  <div class="card">
    <h2>{club['nombre']}</h2>
    <p class="sub">{club['canchas']} canchas techadas · {club['direccion']}</p>
  </div>

  <div class="stats">
    {tiles_html}
  </div>

  <div class="card">
    <h2>Ingreso diario</h2>
    <p class="sub">Línea sólida = periodo actual · línea punteada = periodo anterior · línea gris fina = promedio de {zona['nombre']}</p>
    {svg_ingreso_diario}
  </div>
  <div class="card">
    <h2>Ocupación diaria</h2>
    <p class="sub">Línea sólida = periodo actual · línea punteada = periodo anterior · línea gris fina = promedio de {zona['nombre']}</p>
    {svg_ocupacion_diaria}
  </div>
  <div class="card">
    <h2>Horas reservadas por día</h2>
    <p class="sub">Línea sólida = periodo actual · línea punteada = periodo anterior</p>
    {svg_horas_diarias}
  </div>

  <div class="grid2">
    <div class="card">
      <h2>Ocupación por hora del día</h2>
      {svg_ocup_hora}
    </div>
    <div class="card">
      <h2>Precio por hora del día</h2>
      <p class="sub">Banda = rango min–max · línea = promedio</p>
      {svg_precio_hora}
    </div>
  </div>

  <div class="card">
    <h2>Anticipación de reserva</h2>
    <p class="sub">Ocupación normalizada según cuántos días antes se reservó (100% = nivel del día mismo)</p>
    {svg_anticipacion}
  </div>

  <div class="grid2">
    <div class="card">
      <h2>Temperatura diaria</h2>
      <p class="sub">Línea sólida = máxima · línea punteada = mínima (°C)</p>
      {svg_temp}
    </div>
    <div class="card">
      <h2>Precipitación (cada 3 días)</h2>
      <p class="sub">Milímetros de lluvia</p>
      {svg_precip}
    </div>
  </div>

  <div class="card">
    <h2>Competidores locales</h2>
    <p class="sub">Revés Padel Chapultepec resaltado · ordenado por distancia</p>
    <div class="tablewrap">
    <table>
      <tr><th>Club</th><th>Distancia</th><th>Canchas</th><th>Ingreso</th><th>Ingreso/cancha</th><th>Ocupación</th><th>Rating</th></tr>
      {tabla_filas}
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Ranking — Ingreso total</h2>
    {svg_rank_ingreso}
  </div>
  <div class="card">
    <h2>Ranking — Ingreso por cancha</h2>
    {svg_rank_ingreso_cancha}
  </div>
  <div class="card">
    <h2>Ranking — Ocupación promedio</h2>
    {svg_rank_ocupacion}
  </div>
  <div class="card">
    <h2>Ranking — Precio máximo por hora</h2>
    {svg_rank_precio_max}
  </div>
  <div class="card">
    <h2>Ranking — Rating de Google</h2>
    {svg_rank_rating}
  </div>
</div>
</body>
</html>"""
