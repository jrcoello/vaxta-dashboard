"""
Vaxta Edge — Lógica compartida del reporte mensual por club

Mismo patrón que reportes_semanal.py: calcula todas las métricas desde
Supabase (usuario de solo lectura looker_readonly) y renderiza el HTML.
La usan tanto generar_reporte_mensual.py (línea de comandos) como app.py
(ruta web en Render).

"Mes" = mes calendario completo (día 1 al último día), no un rango de 4
semanas fijas. La sección de Evolución Semanal usa las semanas cuyo lunes
(semana_inicio) cae dentro del mes — puede haber 4 o 5 filas según el mes.

No modifica nada en Supabase, no toca el scraper ni el cron.
"""

import json
import os
from calendar import monthrange
from datetime import date, timedelta

import psycopg2.extras
from jinja2 import Environment, FileSystemLoader

from reportes_semanal import (
    DIAS_ES, MESES_ES, FRANJAS,
    conectar, color_ocupacion, obtener_grupo,
    precio_promedio_semana, precio_promedio_grupo,
    fmt_dinero, construir_heatmap,
)

HERE = os.path.dirname(os.path.abspath(__file__))
_env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates_reportes")))

MESES_ABREV = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def mes_bounds(mes_inicio):
    _, ndias = monthrange(mes_inicio.year, mes_inicio.month)
    return mes_inicio, mes_inicio + timedelta(days=ndias)


def mes_anterior_inicio(mes_inicio):
    if mes_inicio.month == 1:
        return date(mes_inicio.year - 1, 12, 1)
    return date(mes_inicio.year, mes_inicio.month - 1, 1)


def horas_totales_reservadas(cur, tenant_id, ini, fin):
    cur.execute(
        "SELECT SUM(horas_totales) AS ht, SUM(horas_reservadas) AS hr FROM snapshots_ingreso "
        "WHERE tenant_id = %s AND fecha >= %s AND fecha < %s",
        (tenant_id, ini, fin),
    )
    r = cur.fetchone()
    return float(r["ht"]) if r and r["ht"] else 0.0, float(r["hr"]) if r and r["hr"] else 0.0


def horas_totales_reservadas_grupo(cur, tenant_ids, ini, fin):
    if not tenant_ids:
        return 0.0, 0.0
    cur.execute(
        "SELECT SUM(horas_totales) AS ht, SUM(horas_reservadas) AS hr FROM snapshots_ingreso "
        "WHERE tenant_id = ANY(%s) AND fecha >= %s AND fecha < %s",
        (tenant_ids, ini, fin),
    )
    r = cur.fetchone()
    return float(r["ht"]) if r and r["ht"] else 0.0, float(r["hr"]) if r and r["hr"] else 0.0


def ingreso_periodo(cur, tenant_id, ini, fin):
    cur.execute(
        "SELECT SUM(ingreso_estimado_mxn) AS ing FROM ingreso_diario "
        "WHERE tenant_id = %s AND fecha >= %s AND fecha < %s",
        (tenant_id, ini, fin),
    )
    r = cur.fetchone()
    return float(r["ing"]) if r and r["ing"] else 0.0


def ocupacion_zona_semana(cur, tenant_ids, semana_inicio):
    if not tenant_ids:
        return None
    cur.execute(
        "SELECT AVG(ocupacion_prom_pct) AS z FROM ingreso_semanal WHERE tenant_id = ANY(%s) AND semana_inicio = %s",
        (tenant_ids, semana_inicio),
    )
    r = cur.fetchone()
    return float(r["z"]) if r and r["z"] is not None else None


def generar_html_mensual(club_id, mes=None, conn=None):
    """Calcula y renderiza el reporte mensual de un club.

    mes: "YYYY-MM" o None (usa el último mes calendario completo).
    Devuelve (html, club, mes_inicio). Lanza ValueError si el club no
    existe/está excluido o no tiene datos ese mes.
    """
    conn_propia = conn is None
    if conn_propia:
        conn = conectar()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        club, vecinos = obtener_grupo(cur, club_id)
        if club is None:
            raise ValueError(f"Club {club_id} no encontrado o excluido de análisis.")

        if mes is None:
            hoy = date.today()
            primer_dia_mes_actual = date(hoy.year, hoy.month, 1)
            mes_inicio = mes_anterior_inicio(primer_dia_mes_actual)
        elif isinstance(mes, str):
            partes = mes.split("-")
            mes_inicio = date(int(partes[0]), int(partes[1]), 1)
        else:
            mes_inicio = mes

        mes_inicio, mes_fin = mes_bounds(mes_inicio)
        mes_ant_inicio, mes_ant_fin = mes_bounds(mes_anterior_inicio(mes_inicio))
        dias_en_mes = (mes_fin - mes_inicio).days

        ids_grupo = [club["id"]] + [v["id"] for v in vecinos]
        vecino_ids = [v["id"] for v in vecinos]

        cur.execute(
            "SELECT count(*) AS n FROM snapshots_ingreso WHERE tenant_id = %s AND fecha >= %s AND fecha < %s",
            (club_id, mes_inicio, mes_fin),
        )
        if cur.fetchone()["n"] == 0:
            raise ValueError(f"No hay datos de {club['nombre']} para {MESES_ES[mes_inicio.month]} {mes_inicio.year}.")

        # ---------- S1: KPIs ----------
        horas_totales, horas_reservadas = horas_totales_reservadas(cur, club_id, mes_inicio, mes_fin)
        horas_totales_prev, horas_reservadas_prev = horas_totales_reservadas(cur, club_id, mes_ant_inicio, mes_ant_fin)
        ocupacion_actual = (horas_reservadas / horas_totales * 100) if horas_totales else 0.0
        ocupacion_previa = (horas_reservadas_prev / horas_totales_prev * 100) if horas_totales_prev else None

        ingreso_actual = ingreso_periodo(cur, club_id, mes_inicio, mes_fin)
        ingreso_previo_val = ingreso_periodo(cur, club_id, mes_ant_inicio, mes_ant_fin)
        ingreso_previo = ingreso_previo_val if horas_totales_prev else None

        precio_club = precio_promedio_semana(cur, club_id, mes_inicio, mes_fin)
        precio_club_previo = precio_promedio_semana(cur, club_id, mes_ant_inicio, mes_ant_fin)

        ht_zona, hr_zona = horas_totales_reservadas_grupo(cur, vecino_ids, mes_inicio, mes_fin)
        ocupacion_zona = (hr_zona / ht_zona * 100) if ht_zona else None
        precio_zona = precio_promedio_grupo(cur, vecino_ids, mes_inicio, mes_fin)

        mes_ant_nombre = f"{MESES_ES[mes_ant_inicio.month]}"

        def delta_pp(actual, previo):
            if previo is None:
                return "flat", "→", f"Sin dato de {mes_ant_nombre}"
            diff = actual - previo
            if abs(diff) < 0.5:
                return "flat", "→", f"Sin cambio vs {mes_ant_nombre}"
            arrow = "↑" if diff > 0 else "↓"
            cls = "up" if diff > 0 else "down"
            return cls, arrow, f"{diff:+.0f} pp vs {mes_ant_nombre}"

        def delta_money(actual, previo):
            if previo is None:
                return "flat", "→", f"Sin dato de {mes_ant_nombre}"
            diff = actual - previo
            if abs(diff) < 50:
                return "flat", "→", f"Sin cambio vs {mes_ant_nombre}"
            arrow = "↑" if diff > 0 else "↓"
            cls = "up" if diff > 0 else "down"
            return cls, arrow, f"${diff:+,.0f} vs {mes_ant_nombre}"

        oc_cls, oc_arrow, oc_texto = delta_pp(ocupacion_actual, ocupacion_previa)
        kpi_ocupacion = {
            "valor": f"{ocupacion_actual:.0f}",
            "card_class": "positive" if oc_cls == "up" else ("negative" if oc_cls == "down" else "neutral"),
            "delta_class": oc_cls, "delta_arrow": oc_arrow, "delta_texto": oc_texto,
            "zona": f"{ocupacion_zona:.0f}" if ocupacion_zona is not None else "N/D",
            "zona_color": "var(--green)" if ocupacion_zona is not None and ocupacion_actual >= ocupacion_zona else "var(--amber)",
            "zona_texto": (f"{ocupacion_actual - ocupacion_zona:+.0f} pp vs zona" if ocupacion_zona is not None else "Sin vecinos monitoreados"),
        }

        ing_cls, ing_arrow, ing_texto = delta_money(ingreso_actual, ingreso_previo)
        ing_delta_color = "var(--green)" if ing_cls == "up" else ("var(--red)" if ing_cls == "down" else "var(--muted)")
        kpi_ingreso = {
            "card_class": "positive" if ing_cls == "up" else ("negative" if ing_cls == "down" else "neutral"),
            "delta_class": ing_cls, "delta_arrow": ing_arrow, "delta_texto": ing_texto, "delta_color": ing_delta_color,
        }

        pr_cls, pr_arrow, pr_texto = ("flat", "→", f"Sin dato de {mes_ant_nombre}")
        if precio_club is not None and precio_club_previo is not None:
            diff = precio_club - precio_club_previo
            if abs(diff) < 5:
                pr_cls, pr_arrow, pr_texto = "flat", "→", f"Sin cambio vs {mes_ant_nombre}"
            else:
                pr_cls = "up" if diff > 0 else "down"
                pr_arrow = "↑" if diff > 0 else "↓"
                pr_texto = f"${diff:+,.0f} vs {mes_ant_nombre}"
        kpi_precio = {
            "valor": f"{precio_club:.0f}" if precio_club is not None else "N/D",
            "card_class": "warning" if (precio_club and precio_zona and precio_club < precio_zona) else "neutral",
            "delta_class": pr_cls, "delta_arrow": pr_arrow, "delta_texto": pr_texto,
            "zona": f"{precio_zona:.0f}" if precio_zona is not None else "N/D",
            "zona_color": "var(--red)" if (precio_club and precio_zona and precio_club < precio_zona) else "var(--green)",
            "zona_texto": (f"−${precio_zona - precio_club:,.0f} bajo mercado" if precio_club is not None and precio_zona is not None and precio_club < precio_zona
                            else (f"+${precio_club - precio_zona:,.0f} sobre mercado" if precio_club is not None and precio_zona is not None else "Sin vecinos con precio")),
        }

        horas_vacias = max(horas_totales - horas_reservadas, 0)
        horas_para_70 = max(0.70 * horas_totales - horas_reservadas, 0)
        canchas = club["canchas"] or 1
        ingreso = {
            "total": fmt_dinero(ingreso_actual),
            "proyeccion_anual": fmt_dinero(ingreso_actual * 12),
            "por_cancha_dia": fmt_dinero(ingreso_actual / canchas / dias_en_mes),
            "por_cancha_dia_70": fmt_dinero((ingreso_actual + horas_para_70 * (precio_club or 0)) / canchas / dias_en_mes),
            "no_capturado": fmt_dinero(horas_vacias * (precio_club or 0)),
            "impacto_70": fmt_dinero(horas_para_70 * (precio_club or 0)),
        }

        # ---------- Resumen del header (1 línea) ----------
        mejor_o_peor = "un mes fuerte en ocupación" if oc_cls == "up" else ("un mes de menor ocupación" if oc_cls == "down" else "un mes estable en ocupación")
        if precio_club is not None and precio_zona is not None and precio_club < precio_zona - 15:
            resumen_header = f"{MESES_ES[mes_inicio.month].capitalize()} fue {mejor_o_peor} — pero tu precio sigue por debajo de tu zona. Hay ingreso que estás dejando sobre la mesa."
        elif precio_club is not None and precio_zona is not None and precio_club > precio_zona + 15:
            resumen_header = f"{MESES_ES[mes_inicio.month].capitalize()} fue {mejor_o_peor}, con un precio por encima de tu zona — revisa si la demanda lo sigue sosteniendo."
        else:
            resumen_header = f"{MESES_ES[mes_inicio.month].capitalize()} fue {mejor_o_peor}, con precios alineados al mercado de tu zona."

        # ---------- S2: Evolución semanal ----------
        cur.execute(
            "SELECT semana_inicio, ocupacion_prom_pct, ingreso_estimado_mxn FROM ingreso_semanal "
            "WHERE tenant_id = %s AND semana_inicio >= %s AND semana_inicio < %s ORDER BY semana_inicio",
            (club_id, mes_inicio, mes_fin),
        )
        semanas_rows = cur.fetchall()

        cur.execute(
            "SELECT ingreso_estimado_mxn FROM ingreso_semanal WHERE tenant_id = %s AND semana_inicio = %s",
            (club_id, (semanas_rows[0]["semana_inicio"] - timedelta(days=7)) if semanas_rows else mes_inicio),
        )
        r = cur.fetchone()
        ingreso_prev_row = float(r["ingreso_estimado_mxn"]) if r and r["ingreso_estimado_mxn"] else None

        semanas = []
        ingreso_anterior = ingreso_prev_row
        for row in semanas_rows:
            s_inicio = row["semana_inicio"]
            s_fin = s_inicio + timedelta(days=6)
            ocup = float(row["ocupacion_prom_pct"]) if row["ocupacion_prom_pct"] is not None else 0.0
            ing = float(row["ingreso_estimado_mxn"]) if row["ingreso_estimado_mxn"] else 0.0
            precio_sem = precio_promedio_semana(cur, club_id, s_inicio, s_inicio + timedelta(days=7))
            zona_sem = ocupacion_zona_semana(cur, vecino_ids, s_inicio)

            if zona_sem is not None:
                vs_zona_class = "up" if ocup >= zona_sem else "down"
                vs_zona_texto = f"{ocup - zona_sem:+.0f} pp"
            else:
                vs_zona_class, vs_zona_texto = "flat", "N/D"

            if ingreso_anterior is None:
                delta_html = '<span style="color:var(--muted)">—</span>'
            else:
                diff = ing - ingreso_anterior
                if abs(diff) < 50:
                    delta_html = '<span style="color:var(--muted)">Sin cambio</span>'
                elif diff > 0:
                    delta_html = f'<span style="color:var(--green);font-weight:600">↑ ${diff:,.0f}</span>'
                else:
                    delta_html = f'<span style="color:var(--red);font-weight:600">↓ ${abs(diff):,.0f}</span>'

            semanas.append({
                "numero": s_inicio.isocalendar()[1],
                "rango": f"{s_inicio.day} {MESES_ABREV[s_inicio.month]} – {s_fin.day} {MESES_ABREV[s_fin.month]}",
                "ocupacion": f"{ocup:.0f}", "vs_zona_class": vs_zona_class, "vs_zona_texto": vs_zona_texto,
                "precio": f"{precio_sem:.0f}" if precio_sem is not None else "N/D",
                "ingreso": f"{ing:,.0f}", "delta_html": delta_html,
                "_ocup": ocup, "_ing": ing,
            })
            ingreso_anterior = ing

        # Gráfica de línea (dinámica según cuántas semanas tenga el mes)
        zona_ocup_mes = f"{ocupacion_zona:.0f}" if ocupacion_zona is not None else "N/D"
        if semanas:
            valores = [s["_ocup"] for s in semanas]
            todos = valores + ([ocupacion_zona] if ocupacion_zona is not None else [])
            min_v = max(0, (min(todos) // 10) * 10 - 10)
            max_v = min(100, -(-max(todos) // 10) * 10 + 10)
            if max_v <= min_v:
                max_v = min_v + 10
            y_de = lambda v: 113 - (v - min_v) / (max_v - min_v) * (113 - 14)
            linea_y_labels = [{"y": 14, "label": f"{max_v:.0f}"}, {"y": round(14 + (113 - 14) / 3), "label": f"{max_v - (max_v - min_v) / 3:.0f}"},
                               {"y": round(14 + 2 * (113 - 14) / 3), "label": f"{min_v + (max_v - min_v) / 3:.0f}"}, {"y": 113, "label": f"{min_v:.0f}"}]
            n = len(semanas)
            x_start, x_end = (500, 500) if n == 1 else (200, 800)
            linea_club = []
            for i, s in enumerate(semanas):
                x = x_start if n == 1 else x_start + i * (x_end - x_start) / (n - 1)
                y = y_de(s["_ocup"])
                linea_club.append({"x": round(x, 1), "y": round(y, 1), "y_label": round(y - 12, 1), "val": s["ocupacion"], "numero": s["numero"]})
            linea_club_points = " ".join(f"{p['x']},{p['y']}" for p in linea_club)
            linea_zona_y = round(y_de(ocupacion_zona), 1) if ocupacion_zona is not None else 113
            linea_zona_x1, linea_zona_x2 = linea_club[0]["x"], linea_club[-1]["x"]
        else:
            linea_y_labels, linea_club, linea_club_points = [], [], ""
            linea_zona_y, linea_zona_x1, linea_zona_x2 = 113, 200, 800

        # Insight de evolución
        if len(semanas) >= 2:
            racha = 1
            mejor_racha = 1
            for i in range(1, len(semanas)):
                if semanas[i]["_ocup"] > semanas[i - 1]["_ocup"]:
                    racha += 1
                    mejor_racha = max(mejor_racha, racha)
                else:
                    racha = 1
            semana_pico = max(semanas, key=lambda s: s["_ocup"])
            todas_sobre_zona = ocupacion_zona is not None and all(s["_ocup"] >= ocupacion_zona for s in semanas)
            ultima = semanas[-1]
            penultima = semanas[-2]
            cambio_ultima = ultima["_ocup"] - penultima["_ocup"]
            frase_racha = f"Tuviste <strong>{mejor_racha} semanas consecutivas de crecimiento</strong> en el mes, con la semana pico en S{semana_pico['numero']} ({semana_pico['ocupacion']}%). " if mejor_racha >= 2 else ""
            frase_ultima = f"La última semana del mes {'subió' if cambio_ultima > 0 else 'bajó' if cambio_ultima < 0 else 'se mantuvo'} {abs(cambio_ultima):.0f} puntos respecto a la anterior. "
            frase_zona = "El patrón estructural es positivo: estás <strong>consistentemente por encima del promedio de tu zona</strong> en todas las semanas del mes." if todas_sobre_zona else "Tu ocupación varía respecto al promedio de zona semana a semana — vale la pena revisar qué franjas explican esas caídas."
            insight_evolucion = frase_racha + frase_ultima + frase_zona
        else:
            insight_evolucion = "Este mes solo tiene una semana completa de datos — vuelve a revisar el próximo mes para ver la evolución semana a semana."

        # ---------- S3: Ocupación mensual (heatmap + consistencia) ----------
        hm = construir_heatmap(cur, club_id, mes_inicio, mes_fin)
        horas_headers, heatmap, grid = hm["horas_headers"], hm["heatmap"], hm["grid"]
        ocupacion_dias_raw = hm["ocupacion_dias"]
        ocupacion_dias = [{"label": d["label"], "pct": d["pct"], "color_hex": "#1A7A4A" if d["_prom"] >= 65 else ("#D4820A" if d["_prom"] >= 40 else "#C0392B")} for d in ocupacion_dias_raw]

        celdas_fuertes, celdas_debiles = [], []
        for dow in range(1, 8):
            for h in hm["horas_set"]:
                val = grid.get(dow, {}).get(h)
                if val is None:
                    continue
                etiqueta = f"{DIAS_ES[dow-1]} {h.strftime('%H')}h"
                if val >= 70:
                    celdas_fuertes.append((val, etiqueta))
                elif val < 35:
                    celdas_debiles.append((val, etiqueta))
        # Ordenar por qué tan extremas son (más fuerte primero / más débil primero),
        # no por orden de día — si no, un lunes con muchas horas fuertes tapa a un
        # sábado todavía más fuerte que solo aparece una vez en el recorrido.
        celdas_fuertes.sort(key=lambda t: t[0], reverse=True)
        celdas_debiles.sort(key=lambda t: t[0])
        top_fuertes = [etq for _, etq in celdas_fuertes[:6]]
        top_debiles = [etq for _, etq in celdas_debiles[:6]]
        franjas_fuertes = ", ".join(top_fuertes) + ("…" if len(celdas_fuertes) > 6 else "") if celdas_fuertes else "Ninguna franja llegó a 70% de forma sostenida este mes."
        franjas_debiles = ", ".join(top_debiles) + ("…" if len(celdas_debiles) > 6 else "") if celdas_debiles else "No hay franjas consistentemente vacías este mes."

        finde_prom = sum(d["_prom"] for d in ocupacion_dias_raw[4:7]) / 3  # Vie,Sáb,Dom
        entresemana_prom = sum(d["_prom"] for d in ocupacion_dias_raw[0:4]) / 4  # Lun-Jue
        gap_club = finde_prom - entresemana_prom
        ratio_club = f"{finde_prom:.0f}% vs {entresemana_prom:.0f}% — brecha de {gap_club:.0f} pp"

        cur.execute(
            "SELECT EXTRACT(ISODOW FROM fecha)::int AS dow, ocupacion_prom_pct FROM ingreso_diario "
            "WHERE tenant_id = ANY(%s) AND fecha >= %s AND fecha < %s",
            (vecino_ids, mes_inicio, mes_fin) if vecino_ids else ([club_id], mes_inicio, mes_fin),
        )
        zona_dias_rows = cur.fetchall()
        zona_por_dow = {}
        for r in zona_dias_rows:
            if r["ocupacion_prom_pct"] is not None:
                zona_por_dow.setdefault(r["dow"], []).append(float(r["ocupacion_prom_pct"]))
        zona_finde = [v for dow in (5, 6, 7) for v in zona_por_dow.get(dow, [])]
        zona_entresemana = [v for dow in (1, 2, 3, 4) for v in zona_por_dow.get(dow, [])]
        if vecino_ids and zona_finde and zona_entresemana:
            zf = sum(zona_finde) / len(zona_finde)
            ze = sum(zona_entresemana) / len(zona_entresemana)
            ratio_zona = f"{zf:.0f}% vs {ze:.0f}% — brecha de {zf - ze:.0f} pp"
            gap_zona = zf - ze
        else:
            ratio_zona = "Sin datos suficientes de la zona"
            gap_zona = None

        if gap_zona is not None:
            if gap_club > gap_zona + 3:
                insight_consistencia = (
                    f"Tu brecha entre semana / fin de semana ({gap_club:.0f} pp) es <strong>mayor que el promedio de tu zona ({gap_zona:.0f} pp)</strong>. "
                    f"Tus fines de semana son muy fuertes, pero entre semana tienes una oportunidad sin explotar. Una estrategia de precio diferenciado "
                    f"o una promoción de mediodía puede reducir esa brecha sin afectar tu demanda de fin de semana."
                )
            elif gap_club < gap_zona - 3:
                insight_consistencia = (
                    f"Tu brecha entre semana / fin de semana ({gap_club:.0f} pp) es <strong>menor que el promedio de tu zona ({gap_zona:.0f} pp)</strong> — "
                    f"tu demanda entre semana está más balanceada que la de tus competidores, una fortaleza relativa a mantener."
                )
            else:
                insight_consistencia = f"Tu brecha entre semana / fin de semana ({gap_club:.0f} pp) está en línea con el promedio de tu zona ({gap_zona:.0f} pp)."
        else:
            insight_consistencia = f"Tu brecha entre semana / fin de semana este mes fue de {gap_club:.0f} puntos porcentuales."

        # ---------- S4: Precios por franja ----------
        franjas_out = []
        for clave, nombre_display, h_ini, h_fin in FRANJAS:
            cur.execute(
                """
                SELECT ROUND(AVG(precio_efectivo)::numeric,0) AS precio, SUM(horas_reservadas) AS hr
                FROM snapshots_ingreso
                WHERE tenant_id = %s AND fecha >= %s AND fecha < %s
                  AND EXTRACT(HOUR FROM hora_inicio) >= %s AND EXTRACT(HOUR FROM hora_inicio) < %s
                """,
                (club_id, mes_inicio, mes_fin, h_ini, h_fin),
            )
            r_club = cur.fetchone()
            precio_f_club = float(r_club["precio"]) if r_club and r_club["precio"] is not None else None
            horas_reservadas_franja = float(r_club["hr"]) if r_club and r_club["hr"] else 0.0

            precio_f_zona = None
            if vecino_ids:
                cur.execute(
                    """
                    SELECT ROUND(AVG(precio_efectivo)::numeric,0) AS precio
                    FROM snapshots_ingreso
                    WHERE tenant_id = ANY(%s) AND fecha >= %s AND fecha < %s
                      AND EXTRACT(HOUR FROM hora_inicio) >= %s AND EXTRACT(HOUR FROM hora_inicio) < %s
                      AND precio_efectivo IS NOT NULL
                    """,
                    (vecino_ids, mes_inicio, mes_fin, h_ini, h_fin),
                )
                r_zona = cur.fetchone()
                precio_f_zona = float(r_zona["precio"]) if r_zona and r_zona["precio"] is not None else None

            precio_f_club_prev = None
            cur.execute(
                """
                SELECT ROUND(AVG(precio_efectivo)::numeric,0) AS precio FROM snapshots_ingreso
                WHERE tenant_id = %s AND fecha >= %s AND fecha < %s
                  AND EXTRACT(HOUR FROM hora_inicio) >= %s AND EXTRACT(HOUR FROM hora_inicio) < %s
                  AND precio_efectivo IS NOT NULL
                """,
                (club_id, mes_ant_inicio, mes_ant_fin, h_ini, h_fin),
            )
            r_prev = cur.fetchone()
            precio_f_club_prev = float(r_prev["precio"]) if r_prev and r_prev["precio"] is not None else None

            if precio_f_club is None or precio_f_zona is None:
                diff, diff_class, diff_texto, badge_class, badge_texto = None, "diff-neu", "N/D", "badge-amber", "Sin datos"
            else:
                diff = precio_f_club - precio_f_zona
                diff_texto = f"{diff:+,.0f}".replace("+", "+$").replace("-", "-$") if diff != 0 else "$0"
                if diff < -15:
                    diff_class, badge_class, badge_texto = "diff-neg", "badge-amber", "Bajo mercado"
                elif diff > 15:
                    diff_class, badge_class, badge_texto = "diff-pos", "badge-green", "Premium"
                else:
                    diff_class, badge_class, badge_texto = "diff-neu", "badge-green", "Competitivo"

            if precio_f_club is None or precio_f_club_prev is None:
                tendencia = "Sin datos del mes anterior"
            elif abs(precio_f_club - precio_f_club_prev) < 5:
                tendencia = f"Sin cambio vs {mes_ant_nombre}"
            else:
                signo = "+" if precio_f_club > precio_f_club_prev else "-"
                tendencia = f"{signo}${abs(precio_f_club - precio_f_club_prev):,.0f} vs {mes_ant_nombre}"

            franjas_out.append({
                "emoji": nombre_display.split(" ")[0], "nombre": nombre_display.split(" ", 1)[1],
                "horario": f"{h_ini}–{h_fin}h",
                "precio_club": f"${precio_f_club:.0f}" if precio_f_club is not None else "N/D",
                "precio_zona": f"${precio_f_zona:.0f}" if precio_f_zona is not None else "N/D",
                "diff_texto": diff_texto, "diff_class": diff_class,
                "badge_class": badge_class, "badge_texto": badge_texto, "tendencia": tendencia,
                "_diff": diff, "_precio_club": precio_f_club, "_precio_zona": precio_f_zona,
                "_horas_reservadas": horas_reservadas_franja, "_nombre_corto": nombre_display.split(" ", 1)[1],
            })

        # Simulador de impacto
        simulador = []
        impacto_total = 0
        for f in franjas_out:
            if f["_diff"] is not None and f["_diff"] < -15:
                gap = abs(f["_diff"])
                impacto = gap * f["_horas_reservadas"]
                impacto_total += impacto
                simulador.append({
                    "texto": f"Si subes {f['_nombre_corto'].lower()} ${gap:.0f}/hr (${f['_precio_club']:.0f}→${f['_precio_zona']:.0f}):",
                    "impacto": f"+${impacto:,.0f}/mes estimados",
                    "nota": "asumiendo que la ocupación se mantiene",
                })
        if len(simulador) >= 2:
            simulador.append({
                "texto": "Impacto combinado de todos los ajustes:",
                "impacto": f"+${impacto_total:,.0f}/mes",
                "nota": f"~${impacto_total*12:,.0f}/año sin clientes nuevos",
            })
        if not simulador:
            simulador.append({
                "texto": "Tus precios ya están alineados o por encima del mercado en todas las franjas.",
                "impacto": "Sin oportunidad inmediata",
                "nota": "revisa el reporte semanal para ajustes finos",
            })

        # ---------- S5: Comparativo de zona + mapa ----------
        ocup_por_id, precio_por_id = {}, {}
        for tid in ids_grupo:
            ht_c, hr_c = horas_totales_reservadas(cur, tid, mes_inicio, mes_fin)
            if ht_c > 0:
                ocup_por_id[tid] = hr_c / ht_c * 100
            p = precio_promedio_semana(cur, tid, mes_inicio, mes_fin)
            if p is not None:
                precio_por_id[tid] = p

        nombres_por_id = {club["id"]: club["nombre"]}
        canchas_por_id = {club["id"]: club["canchas"]}
        lat_por_id = {club["id"]: club.get("lat")}
        lng_por_id = {club["id"]: club.get("lon")}
        for v in vecinos:
            nombres_por_id[v["id"]] = v["nombre"]
            canchas_por_id[v["id"]] = v["canchas"]

        filas = [{"id": tid, "nombre": nombres_por_id[tid], "ocupacion": ocup_por_id[tid],
                  "precio": precio_por_id.get(tid), "canchas": canchas_por_id[tid]}
                 for tid in ids_grupo if tid in ocup_por_id]
        filas_ranked = sorted(filas, key=lambda x: x["ocupacion"], reverse=True)
        for i, f in enumerate(filas_ranked, 1):
            f["rank"] = i
        posicion_ocupacion = next((f["rank"] for f in filas_ranked if f["id"] == club_id), "N/D")

        comparativo = []
        for f in filas_ranked:
            color = "#1A7A4A" if (ocupacion_zona is None or f["ocupacion"] >= ocupacion_zona) else ("#D4820A" if ocupacion_zona is not None and f["ocupacion"] >= ocupacion_zona - 10 else "#C0392B")
            comparativo.append({
                "rank": f["rank"], "nombre": f["nombre"], "es_mi_club": f["id"] == club_id,
                "ocupacion": f"{f['ocupacion']:.0f}", "color": "var(--teal)" if f["id"] == club_id else color,
                "precio": f"${f['precio']:.0f}" if f["precio"] is not None else "N/D", "canchas": f["canchas"],
            })

        filas_vecinos = [f for f in filas if f["id"] != club_id]
        filas_vecinos_con_precio = [f for f in filas_vecinos if f["precio"] is not None]
        prom_ocup_zona = sum(f["ocupacion"] for f in filas_vecinos) / len(filas_vecinos) if filas_vecinos else 0
        prom_precio_zona = sum(f["precio"] for f in filas_vecinos_con_precio) / len(filas_vecinos_con_precio) if filas_vecinos_con_precio else 0
        promedio_zona = {"ocupacion": f"{prom_ocup_zona:.0f}", "precio": f"${prom_precio_zona:.0f}"}

        if filas_ranked and filas_ranked[0]["id"] != club_id:
            lider = filas_ranked[0]
            brecha = lider["ocupacion"] - ocupacion_actual
            insight_mercado = (
                f"Este mes eres el <strong>#{posicion_ocupacion} en ocupación</strong> de tu zona. La brecha con el #1 ({lider['nombre']}, {lider['ocupacion']:.0f}%) "
                f"es de {brecha:.0f} pp. En precio estás en la posición correspondiente a ${precio_club:.0f} (zona: ${prom_precio_zona:.0f}) — "
                f"{'cobrando menos que clubes con ocupación similar, lo que puede explicar tu buena demanda' if precio_club is not None and precio_club < prom_precio_zona else 'en línea con el mercado'}."
            )
        else:
            insight_mercado = f"Este mes eres <strong>#1 en ocupación</strong> de tu zona ({ocupacion_actual:.0f}%), {ocupacion_actual - prom_ocup_zona:.0f} puntos sobre el promedio."

        clubes_mapa = []
        if club.get("lat") and club.get("lon"):
            clubes_mapa.append({
                "nombre": club["nombre"], "occ": round(ocup_por_id.get(club_id, 0)),
                "precio": round(precio_por_id[club_id]) if club_id in precio_por_id else None,
                "canchas": club["canchas"], "lat": club["lat"], "lng": club["lon"],
                "color": "#1A8A8A", "size": 20, "border": "#ffffff", "bw": 3, "esClub": True,
            })
        for v in vecinos:
            cur.execute("SELECT lat, lon FROM clubs WHERE tenant_id = %s", (v["id"],))
            r = cur.fetchone()
            if not r or r["lat"] is None or r["lon"] is None or v["id"] not in ocup_por_id:
                continue
            occ_v = ocup_por_id[v["id"]]
            color = "#1A7A4A" if (ocupacion_zona is None or occ_v >= ocupacion_zona) else ("#D4820A" if ocupacion_zona is not None and occ_v >= ocupacion_zona - 10 else "#C0392B")
            clubes_mapa.append({
                "nombre": v["nombre"], "occ": round(occ_v),
                "precio": round(precio_por_id[v["id"]]) if v["id"] in precio_por_id else None,
                "canchas": v["canchas"], "lat": r["lat"], "lng": r["lon"],
                "color": color, "size": 14 + min(v["canchas"] or 0, 10), "border": "#333333", "bw": 1.5, "esClub": False,
            })
        if clubes_mapa:
            mapa_centro_lat = sum(c["lat"] for c in clubes_mapa) / len(clubes_mapa)
            mapa_centro_lng = sum(c["lng"] for c in clubes_mapa) / len(clubes_mapa)
        else:
            mapa_centro_lat, mapa_centro_lng = club.get("lat") or 19.4, club.get("lon") or -99.15

        # ---------- S7: Decisión estratégica ----------
        candidatas = [f for f in franjas_out if f["_diff"] is not None and f["_diff"] < -10]
        precio_bajo_zona = precio_club is not None and precio_zona is not None and precio_club < prom_precio_zona
        ocup_sobre_zona = ocupacion_zona is not None and ocupacion_actual >= ocupacion_zona

        if candidatas and precio_bajo_zona:
            peor = min(candidatas, key=lambda f: f["_diff"])
            es_lider = posicion_ocupacion == 1
            if es_lider:
                titulo_decision = "Tienes ocupación de líder con precio de retador — ¿hasta cuándo?"
            elif ocup_sobre_zona:
                titulo_decision = f"Estás #{posicion_ocupacion} en ocupación con uno de los precios más bajos — hay margen para subir"
            else:
                titulo_decision = "Tu precio está frenando tu ingreso, no tu ocupación"
            decision = {
                "titulo": titulo_decision,
                "diagnostico": (
                    f"Tu ocupación mensual ({ocupacion_actual:.0f}%) te ubica en la posición #{posicion_ocupacion} de {len(filas_ranked)} clubes de tu zona"
                    f"{' — el mejor lugar' if es_lider else (', por encima del promedio de zona (' + f'{ocupacion_zona:.0f}%)' if ocup_sobre_zona else ', cerca del promedio de tu zona (' + f'{ocupacion_zona:.0f}%)')}, "
                    f"y tu precio promedio (${precio_club:.0f}) sigue por debajo del promedio de zona (${prom_precio_zona:.0f}). Eso significa que tu demanda está probada — "
                    f"tienes clientes que eligen tu club incluso sin ser el más barato en todas las franjas. La pregunta no es si puedes subir el precio: "
                    f"ya lo tienes validado en las franjas donde tu precio está alineado con el mercado. La pregunta es cuánto ingreso estás dejando en la mesa "
                    f"cada mes que no ajustas las franjas de {peor['_nombre_corto'].lower()}."
                ),
                "camino_a_label": "Ajuste gradual de precios",
                "camino_a_texto": (
                    f"Subir {peor['_nombre_corto'].lower()} ${abs(peor['_diff']):.0f}/hr el próximo mes. Monitorear impacto en ocupación semana a semana en el reporte semanal. "
                    f"Si la ocupación baja más de 5 pp en esa franja, revertir. Si se mantiene, subir otro paso el mes siguiente. "
                    f"<strong>Impacto esperado: +${impacto_total:,.0f}/mes sin clientes nuevos.</strong>"
                ),
                "camino_b_label": "Mantener precio, crecer volumen",
                "camino_b_texto": (
                    "No ajustar precios y usar la ventaja de precio para acelerar captación de nuevos clientes. El riesgo: cuando abra el próximo club en tu zona, "
                    "probablemente lo hará con precios competitivos y tú no tendrás margen para bajar. <strong>Impacto esperado: crecimiento más lento, más exposición competitiva.</strong>"
                ),
                "paso": (
                    f"Este mes, ajusta el precio de la franja {peor['_nombre_corto'].lower()} ({peor['horario']}) de {peor['precio_club']} a ~${peor['_precio_club']+abs(peor['_diff'])*0.7:.0f}. "
                    f"Revisa el impacto en los próximos 2 reportes semanales. Si la ocupación no cae más de 5 pp, mantén el precio y replica el ajuste en otra franja el mes siguiente."
                ),
            }
        elif ocup_sobre_zona and not precio_bajo_zona:
            decision = {
                "titulo": "Posición sólida — el reto ahora es la retención, no el precio",
                "diagnostico": (
                    f"Tu ocupación ({ocupacion_actual:.0f}%) está por encima del promedio de tu zona y tu precio (${precio_club:.0f}) ya está alineado con el mercado "
                    f"(zona: ${prom_precio_zona:.0f}). No hay una oportunidad de precio obvia este mes — el foco debería estar en sostener la demanda que ya tienes."
                ),
                "camino_a_label": "Mantener y monitorear",
                "camino_a_texto": "Sostener el precio actual y usar los reportes semanales para detectar temprano cualquier caída de demanda en franjas específicas.",
                "camino_b_label": "Explorar franjas débiles",
                "camino_b_texto": f"Revisar la sección de Ocupación Mensual de este reporte para identificar franjas con ocupación baja y evaluar promociones puntuales ahí.",
                "paso": "Revisa el reporte del próximo mes para confirmar que la ocupación y el precio se mantienen alineados con el mercado.",
            }
        else:
            decision = {
                "titulo": "Ocupación por debajo de zona — el precio no es el problema principal",
                "diagnostico": (
                    f"Tu ocupación ({ocupacion_actual:.0f}%) está por debajo del promedio de tu zona ({ocupacion_zona:.0f}% aprox. si hay datos). "
                    f"Antes de tocar precio, vale la pena entender qué franjas específicas están débiles — revisa la sección de Ocupación Mensual de este reporte."
                ),
                "camino_a_label": "Diagnóstico de demanda",
                "camino_a_texto": "Identifica las franjas más débiles del mes (sección S3) y evalúa si el problema es precio, visibilidad en Playtomic, o competencia directa cercana.",
                "camino_b_label": "Promoción táctica",
                "camino_b_texto": "Activa una promoción de corto plazo (2-3 semanas) en la franja más débil para probar si el precio es el freno, antes de hacer un cambio permanente.",
                "paso": "Elige una sola franja débil y prueba un ajuste (precio o promoción) durante 2 semanas, comparando resultados en los reportes semanales.",
            }

        # ---------- Render ----------
        hoy = date.today()
        fecha_generado = f"{hoy.day} de {MESES_ES[hoy.month]} de {hoy.year}"

        tpl = _env.get_template("mensual.html.j2")
        html = tpl.render(
            club=club, mes_nombre=MESES_ES[mes_inicio.month].capitalize(), anio=mes_inicio.year,
            fecha_generado=fecha_generado, resumen_header=resumen_header,
            kpi_ocupacion=kpi_ocupacion, kpi_ingreso=kpi_ingreso, kpi_precio=kpi_precio, ingreso=ingreso,
            semanas=semanas, linea_y_labels=linea_y_labels, linea_club=linea_club, linea_club_points=linea_club_points,
            linea_zona_y=linea_zona_y, linea_zona_x1=linea_zona_x1, linea_zona_x2=linea_zona_x2,
            zona_ocup_mes=zona_ocup_mes, insight_evolucion=insight_evolucion,
            horas_headers=horas_headers, heatmap=heatmap, ocupacion_dias=ocupacion_dias,
            franjas_fuertes=franjas_fuertes, franjas_debiles=franjas_debiles,
            ratio_club=ratio_club, ratio_zona=ratio_zona, insight_consistencia=insight_consistencia,
            franjas=franjas_out, simulador=simulador,
            comparativo=comparativo, promedio_zona=promedio_zona, insight_mercado=insight_mercado,
            clubes_mapa_json=json.dumps(clubes_mapa, ensure_ascii=False),
            mapa_centro_lat=mapa_centro_lat, mapa_centro_lng=mapa_centro_lng,
            decision=decision,
        )

        return html, club, mes_inicio
    finally:
        if conn_propia:
            conn.close()
