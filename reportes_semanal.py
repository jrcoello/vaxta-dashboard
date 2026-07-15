"""
Vaxta Edge — Lógica compartida del reporte semanal por club

Calcula todas las métricas del reporte semanal desde Supabase (usuario de
solo lectura looker_readonly, misma credencial que usa app.py) y renderiza
el HTML. La usan tanto generar_reporte_semanal.py (línea de comandos, para
generar y guardar un archivo) como app.py (ruta web en Render, para servir
el reporte de cualquier club bajo demanda).

No modifica nada en Supabase, no toca el scraper ni el cron.
"""

import os
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from jinja2 import Environment, FileSystemLoader

HERE = os.path.dirname(os.path.abspath(__file__))

DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

FRANJAS = [
    ("mañana", "☀️ Mañana", 6, 12),
    ("mediodia", "🌤 Mediodía", 12, 16),
    ("tarde", "🌇 Tarde", 16, 20),
    ("noche", "🌙 Noche", 20, 23),
]

_env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates_reportes")))


def conectar():
    pw = os.environ["LOOKER_READONLY_PASSWORD"]
    dsn = (
        f"postgresql://looker_readonly.foaryubaknxlkwxxndnn:{pw}"
        f"@aws-1-us-east-2.pooler.supabase.com:6543/postgres"
    )
    return psycopg2.connect(dsn)


def color_ocupacion(pct):
    if pct is None:
        return "#e0e0e0"
    if pct < 15: return "#fad9d6"
    if pct < 30: return "#fdefd3"
    if pct < 40: return "#e8f5f0"
    if pct < 50: return "#b8e0d4"
    if pct < 65: return "#7fc8b4"
    if pct < 80: return "#52b89a"
    if pct < 90: return "#2ea882"
    return "#1A8A8A"


def fmt_dinero(n):
    if n is None:
        return "0"
    return f"{n:,.0f}"


def obtener_grupo(cur, club_id):
    cur.execute(
        "SELECT tenant_id AS id, nombre, mercado, ciudad, canchas FROM clubs "
        "WHERE tenant_id = %s AND excluir_analisis = false",
        (club_id,),
    )
    club = cur.fetchone()
    if not club:
        return None, None

    cur.execute(
        """
        SELECT c.tenant_id AS id, c.nombre, c.canchas
        FROM club_vecinos v
        JOIN clubs c ON c.tenant_id = v.vecino_id
        WHERE v.club_id = %s
        ORDER BY v.rank
        """,
        (club_id,),
    )
    vecinos = cur.fetchall()
    return club, vecinos


def semana_kpi(cur, tenant_id, semana_inicio):
    cur.execute(
        "SELECT ingreso_estimado_mxn, ocupacion_prom_pct, horas_reservadas "
        "FROM ingreso_semanal WHERE tenant_id = %s AND semana_inicio = %s",
        (tenant_id, semana_inicio),
    )
    return cur.fetchone()


def precio_promedio_semana(cur, tenant_id, semana_inicio, semana_fin):
    cur.execute(
        """
        SELECT ROUND(AVG(precio_efectivo)::numeric, 0) AS precio
        FROM snapshots_ingreso
        WHERE tenant_id = %s AND fecha >= %s AND fecha < %s AND precio_efectivo IS NOT NULL
        """,
        (tenant_id, semana_inicio, semana_fin),
    )
    r = cur.fetchone()
    return float(r["precio"]) if r and r["precio"] is not None else None


def precio_promedio_grupo(cur, tenant_ids, semana_inicio, semana_fin):
    """Promedio de zona = promedio de los promedios de cada club (no un promedio
    ponderado por cantidad de snapshots), para que sea consistente con la tabla
    comparativa de S4, que se calcula club por club."""
    if not tenant_ids:
        return None
    cur.execute(
        """
        SELECT AVG(precio_efectivo) AS precio
        FROM snapshots_ingreso
        WHERE tenant_id = ANY(%s) AND fecha >= %s AND fecha < %s AND precio_efectivo IS NOT NULL
        GROUP BY tenant_id
        """,
        (tenant_ids, semana_inicio, semana_fin),
    )
    precios = [float(r["precio"]) for r in cur.fetchall() if r["precio"] is not None]
    return sum(precios) / len(precios) if precios else None


def construir_heatmap(cur, tenant_id, fecha_ini, fecha_fin):
    """Heatmap ocupación por día-de-semana × hora en [fecha_ini, fecha_fin).
    Reusado por el reporte semanal (una semana) y el mensual (mes completo,
    promediando todos los días que caen en cada día-de-semana/hora)."""
    cur.execute(
        """
        SELECT EXTRACT(ISODOW FROM fecha)::int AS dow, hora_inicio, ocupacion_pct
        FROM snapshots_ingreso
        WHERE tenant_id = %s AND fecha >= %s AND fecha < %s
        ORDER BY dow, hora_inicio
        """,
        (tenant_id, fecha_ini, fecha_fin),
    )
    rows = cur.fetchall()

    horas_set = sorted({r["hora_inicio"] for r in rows})
    horas_headers = [h.strftime("%H") for h in horas_set]
    grid_valores = {}
    for r in rows:
        if r["ocupacion_pct"] is None:
            continue
        grid_valores.setdefault(r["dow"], {}).setdefault(r["hora_inicio"], []).append(float(r["ocupacion_pct"]))

    grid = {}
    heatmap = []
    hora_pico = {"val": -1, "label": "N/D"}
    hora_valle = {"val": 101, "label": "N/D"}
    ocupacion_dias = []
    dias_sobre_70_lista = []

    for dow in range(1, 8):
        label = DIAS_ES[dow - 1]
        cells = []
        valores_dia = []
        for h in horas_set:
            valores = grid_valores.get(dow, {}).get(h)
            val = sum(valores) / len(valores) if valores else None
            grid.setdefault(dow, {})[h] = val
            cells.append({
                "val": f"{val:.0f}%" if val is not None else "s/d",
                "color": color_ocupacion(val),
            })
            if val is not None:
                valores_dia.append(val)
                if val > hora_pico["val"]:
                    hora_pico = {"val": val, "label": f"{label} {h.strftime('%H')}h"}
                if val < hora_valle["val"]:
                    hora_valle = {"val": val, "label": f"{label} {h.strftime('%H')}h"}
        heatmap.append({"label": label, "cells": cells})
        prom_dia = sum(valores_dia) / len(valores_dia) if valores_dia else 0
        ocupacion_dias.append({"label": label, "pct": f"{prom_dia:.0f}", "color": color_ocupacion(prom_dia), "_prom": prom_dia})
        if prom_dia > 70:
            dias_sobre_70_lista.append(label)

    hora_pico["val"] = f"{hora_pico['val']:.0f}" if hora_pico["val"] >= 0 else "N/D"
    hora_valle["val"] = f"{hora_valle['val']:.0f}" if hora_valle["val"] <= 100 else "N/D"
    dias_sobre_70 = {"count": len(dias_sobre_70_lista), "lista": ", ".join(dias_sobre_70_lista) if dias_sobre_70_lista else "Ninguno"}

    return {
        "horas_set": horas_set, "horas_headers": horas_headers, "grid": grid,
        "heatmap": heatmap, "ocupacion_dias": ocupacion_dias,
        "hora_pico": hora_pico, "hora_valle": hora_valle, "dias_sobre_70": dias_sobre_70,
    }


def generar_html_semanal(club_id, semana_inicio=None, conn=None):
    """Calcula y renderiza el reporte semanal de un club.

    Devuelve (html, club, semana_inicio). Lanza ValueError con un mensaje
    en español si el club no existe/está excluido o no tiene datos.
    """
    conn_propia = conn is None
    if conn_propia:
        conn = conectar()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        club, vecinos = obtener_grupo(cur, club_id)
        if club is None:
            raise ValueError(f"Club {club_id} no encontrado o excluido de análisis.")

        if semana_inicio is None:
            cur.execute(
                "SELECT MAX(semana_inicio) AS m FROM ingreso_semanal WHERE tenant_id = %s",
                (club_id,),
            )
            semana_inicio = cur.fetchone()["m"]
            if semana_inicio is None:
                raise ValueError(f"No hay datos de ingreso_semanal para {club['nombre']}.")
        elif isinstance(semana_inicio, str):
            semana_inicio = date.fromisoformat(semana_inicio)

        semana_fin_exclusiva = semana_inicio + timedelta(days=7)
        semana_anterior_inicio = semana_inicio - timedelta(days=7)
        semana_anterior_fin_exclusiva = semana_inicio

        ids_grupo = [club["id"]] + [v["id"] for v in vecinos]

        # ---------- S1: KPIs ----------
        kpi_actual = semana_kpi(cur, club_id, semana_inicio)
        kpi_previo = semana_kpi(cur, club_id, semana_anterior_inicio)

        ocupacion_actual = float(kpi_actual["ocupacion_prom_pct"]) if kpi_actual else 0.0
        ocupacion_previa = float(kpi_previo["ocupacion_prom_pct"]) if kpi_previo else None
        ingreso_actual = float(kpi_actual["ingreso_estimado_mxn"]) if kpi_actual and kpi_actual["ingreso_estimado_mxn"] else 0.0
        ingreso_previo = float(kpi_previo["ingreso_estimado_mxn"]) if kpi_previo and kpi_previo["ingreso_estimado_mxn"] else None

        precio_club = precio_promedio_semana(cur, club_id, semana_inicio, semana_fin_exclusiva)
        precio_club_previo = precio_promedio_semana(cur, club_id, semana_anterior_inicio, semana_anterior_fin_exclusiva)
        vecino_ids = [v["id"] for v in vecinos]
        precio_zona = precio_promedio_grupo(cur, vecino_ids, semana_inicio, semana_fin_exclusiva) if vecino_ids else None
        ocupacion_zona_row = None
        if vecino_ids:
            cur.execute(
                "SELECT AVG(ocupacion_prom_pct) AS z FROM ingreso_semanal WHERE tenant_id = ANY(%s) AND semana_inicio = %s",
                (vecino_ids, semana_inicio),
            )
            ocupacion_zona_row = cur.fetchone()
        ocupacion_zona = float(ocupacion_zona_row["z"]) if ocupacion_zona_row and ocupacion_zona_row["z"] is not None else None

        def delta_pp(actual, previo):
            if previo is None:
                return "flat", "→", "Sin dato de la semana anterior"
            diff = actual - previo
            if abs(diff) < 0.5:
                return "flat", "→", "Sin cambio vs semana anterior"
            arrow = "↑" if diff > 0 else "↓"
            cls = "up" if diff > 0 else "down"
            return cls, arrow, f"{diff:+.0f} pp vs semana anterior"

        def delta_money(actual, previo):
            if previo is None:
                return "flat", "→", "Sin dato de la semana anterior"
            diff = actual - previo
            if abs(diff) < 50:
                return "flat", "→", "Sin cambio vs semana anterior"
            arrow = "↑" if diff > 0 else "↓"
            cls = "up" if diff > 0 else "down"
            return cls, arrow, f"${diff:+,.0f} vs semana anterior"

        oc_cls, oc_arrow, oc_texto = delta_pp(ocupacion_actual, ocupacion_previa)
        kpi_ocupacion = {
            "valor": f"{ocupacion_actual:.0f}",
            "card_class": "positive" if oc_cls == "up" else ("negative" if oc_cls == "down" else "neutral"),
            "delta_class": oc_cls, "delta_arrow": oc_arrow, "delta_texto": oc_texto,
            "zona": f"{ocupacion_zona:.0f}" if ocupacion_zona is not None else "N/D",
            "zona_color": "var(--green)" if ocupacion_zona is not None and ocupacion_actual >= ocupacion_zona else "var(--amber)",
            "zona_texto": (f"{ocupacion_actual - ocupacion_zona:+.0f} pp sobre zona" if ocupacion_zona is not None else "Sin vecinos monitoreados"),
        }

        ing_cls, ing_arrow, ing_texto = delta_money(ingreso_actual, ingreso_previo)
        kpi_ingreso = {
            "valor": f"{ingreso_actual/1000:.0f}" if ingreso_actual >= 1000 else f"{ingreso_actual:.0f}",
            "unidad": "k" if ingreso_actual >= 1000 else "",
            "card_class": "positive" if ing_cls == "up" else ("negative" if ing_cls == "down" else "neutral"),
            "delta_class": ing_cls, "delta_arrow": ing_arrow, "delta_texto": ing_texto,
        }

        pr_cls, pr_arrow, pr_texto = ("flat", "→", "Sin dato de la semana anterior")
        if precio_club is not None and precio_club_previo is not None:
            diff = precio_club - precio_club_previo
            if abs(diff) < 5:
                pr_cls, pr_arrow, pr_texto = "flat", "→", "Sin cambio vs semana anterior"
            else:
                pr_cls = "up" if diff > 0 else "down"
                pr_arrow = "↑" if diff > 0 else "↓"
                pr_texto = f"${diff:+,.0f} vs semana anterior"
        kpi_precio = {
            "valor": f"{precio_club:.0f}" if precio_club is not None else "N/D",
            "card_class": "neutral",
            "delta_class": pr_cls, "delta_arrow": pr_arrow, "delta_texto": pr_texto,
            "zona": f"{precio_zona:.0f}" if precio_zona is not None else "N/D",
            "zona_color": "var(--amber)" if (precio_club and precio_zona and precio_club < precio_zona) else "var(--green)",
            "zona_texto": (f"${precio_club - precio_zona:+,.0f} vs mercado" if precio_club is not None and precio_zona is not None else "Sin vecinos con precio"),
        }

        # ---------- S2: Heatmap + bar chart ----------
        hm = construir_heatmap(cur, club_id, semana_inicio, semana_fin_exclusiva)
        horas_set, horas_headers, grid = hm["horas_set"], hm["horas_headers"], hm["grid"]
        heatmap, ocupacion_dias = hm["heatmap"], hm["ocupacion_dias"]
        hora_pico, hora_valle, dias_sobre_70 = hm["hora_pico"], hm["hora_valle"], hm["dias_sobre_70"]
        if dias_sobre_70["lista"] == "Ninguno":
            dias_sobre_70["lista"] = "Ninguno esta semana"

        # Franjas bajas (<20%) entre semana (Lun-Vie), para el insight
        franjas_bajas = []
        for dow in range(1, 6):
            for h in horas_set:
                val = grid.get(dow, {}).get(h)
                if val is not None and val < 20:
                    franjas_bajas.append(f"{DIAS_ES[dow-1]} {h.strftime('%H')}h")
        if franjas_bajas:
            insight_ocupacion = (
                f"Tienes <strong>{len(franjas_bajas)} franjas entre semana con ocupación menor al 20%</strong> "
                f"({', '.join(franjas_bajas[:4])}{'…' if len(franjas_bajas) > 4 else ''}). "
                f"Considera activar una promoción en esos horarios — una cancha con descuento en esas franjas "
                f"es más rentable que tenerla vacía a precio completo."
            )
        else:
            insight_ocupacion = "No se detectaron franjas entre semana con ocupación crítica (&lt;20%) esta semana. Buen equilibrio de demanda."

        # ---------- S3: Precios por franja ----------
        franjas_out = []
        for clave, nombre_display, h_ini, h_fin in FRANJAS:
            cur.execute(
                """
                SELECT ROUND(AVG(precio_efectivo)::numeric,0) AS precio, AVG(ocupacion_pct) AS ocup
                FROM snapshots_ingreso
                WHERE tenant_id = %s AND fecha >= %s AND fecha < %s
                  AND EXTRACT(HOUR FROM hora_inicio) >= %s AND EXTRACT(HOUR FROM hora_inicio) < %s
                  AND precio_efectivo IS NOT NULL
                """,
                (club_id, semana_inicio, semana_fin_exclusiva, h_ini, h_fin),
            )
            r_club = cur.fetchone()
            precio_f_club = float(r_club["precio"]) if r_club and r_club["precio"] is not None else None
            ocup_f_club = float(r_club["ocup"]) if r_club and r_club["ocup"] is not None else 0.0

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
                    (vecino_ids, semana_inicio, semana_fin_exclusiva, h_ini, h_fin),
                )
                r_zona = cur.fetchone()
                precio_f_zona = float(r_zona["precio"]) if r_zona and r_zona["precio"] is not None else None

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

            franjas_out.append({
                "emoji": nombre_display.split(" ")[0], "nombre": nombre_display.split(" ", 1)[1],
                "horario": f"{h_ini}h – {h_fin}h",
                "precio_club": f"${precio_f_club:.0f}" if precio_f_club is not None else "N/D",
                "precio_zona": f"${precio_f_zona:.0f}" if precio_f_zona is not None else "N/D",
                "diff_texto": diff_texto, "diff_class": diff_class,
                "badge_class": badge_class, "badge_texto": badge_texto,
                "_diff": diff, "_ocup": ocup_f_club, "_precio_club": precio_f_club, "_precio_zona": precio_f_zona,
                "_clave": clave, "_nombre_corto": nombre_display.split(" ", 1)[1],
            })

        franjas_bajo_mercado = [f for f in franjas_out if f["_diff"] is not None and f["_diff"] < -15]
        if franjas_bajo_mercado:
            nombres_f = ", ".join(f["_nombre_corto"] for f in franjas_bajo_mercado)
            insight_precios = (
                f"Tu precio de <strong>{nombres_f}</strong> está por debajo del promedio de tu zona. "
                f"Revisa si tu ocupación en esos horarios sostiene un incremento sin perder reservas."
            )
        else:
            insight_precios = "Tus precios están alineados o por encima del promedio de tu zona en todas las franjas. No se detecta oportunidad inmediata de ajuste al alza."

        # ---------- S4: Comparativo de zona ----------
        cur.execute(
            "SELECT tenant_id AS id, ocupacion_prom_pct FROM ingreso_semanal WHERE tenant_id = ANY(%s) AND semana_inicio = %s",
            (ids_grupo, semana_inicio),
        )
        ocupacion_por_id = {r["id"]: float(r["ocupacion_prom_pct"]) for r in cur.fetchall() if r["ocupacion_prom_pct"] is not None}

        precio_por_id = {}
        for tid in ids_grupo:
            p = precio_promedio_semana(cur, tid, semana_inicio, semana_fin_exclusiva)
            if p is not None:
                precio_por_id[tid] = p

        nombres_por_id = {club["id"]: club["nombre"]}
        canchas_por_id = {club["id"]: club["canchas"]}
        for v in vecinos:
            nombres_por_id[v["id"]] = v["nombre"]
            canchas_por_id[v["id"]] = v["canchas"]

        filas = []
        for tid in ids_grupo:
            if tid in ocupacion_por_id:
                filas.append({"id": tid, "nombre": nombres_por_id[tid], "ocupacion": ocupacion_por_id[tid],
                              "precio": precio_por_id.get(tid), "canchas": canchas_por_id[tid]})

        filas_ranked_ocup = sorted(filas, key=lambda x: x["ocupacion"], reverse=True)
        for i, f in enumerate(filas_ranked_ocup, 1):
            f["rank"] = i

        posicion_ocupacion = next((f["rank"] for f in filas_ranked_ocup if f["id"] == club_id), "N/D")

        filas_con_precio = [f for f in filas if f["precio"] is not None]
        filas_ranked_precio = sorted(filas_con_precio, key=lambda x: x["precio"], reverse=True)
        posicion_precio = next((i for i, f in enumerate(filas_ranked_precio, 1) if f["id"] == club_id), "N/D")

        comparativo = []
        for f in filas_ranked_ocup:
            comparativo.append({
                "rank": f["rank"], "nombre": f["nombre"], "es_mi_club": f["id"] == club_id,
                "ocupacion": f"{f['ocupacion']:.0f}", "ocupacion_color": color_ocupacion(f["ocupacion"]),
                "precio": f"${f['precio']:.0f}" if f["precio"] is not None else "N/D",
                "canchas": f["canchas"],
            })

        # "Promedio de zona" excluye siempre al propio club — es el benchmark de
        # los competidores, no un promedio del grupo completo (así coincide con
        # el "Promedio zona" ya mostrado en el KPI de S1, que usa la misma regla).
        filas_vecinos = [f for f in filas if f["id"] != club_id]
        filas_vecinos_con_precio = [f for f in filas_vecinos if f["precio"] is not None]
        prom_ocup_zona = sum(f["ocupacion"] for f in filas_vecinos) / len(filas_vecinos) if filas_vecinos else 0
        prom_precio_zona = sum(f["precio"] for f in filas_vecinos_con_precio) / len(filas_vecinos_con_precio) if filas_vecinos_con_precio else 0
        promedio_zona = {"ocupacion": f"{prom_ocup_zona:.0f}", "precio": f"${prom_precio_zona:.0f}"}

        if precio_club is not None and prom_precio_zona:
            if precio_club < prom_precio_zona and ocupacion_actual >= prom_ocup_zona:
                insight_mercado = (
                    f"Eres el <strong>#{posicion_ocupacion} en ocupación</strong> de tu zona, con {ocupacion_actual - prom_ocup_zona:+.0f} puntos vs. el promedio. "
                    f"Sin embargo tu precio promedio (${precio_club:.0f}) está por debajo del promedio de zona (${prom_precio_zona:.0f}) — "
                    f"hay espacio para subir precio sin sacrificar demanda."
                )
            elif precio_club > prom_precio_zona and ocupacion_actual < prom_ocup_zona:
                insight_mercado = (
                    f"Tu precio promedio (${precio_club:.0f}) está por encima del promedio de zona (${prom_precio_zona:.0f}), "
                    f"pero tu ocupación (#{posicion_ocupacion} de {len(filas)}) está por debajo del promedio. Vale la pena revisar si el precio está limitando la demanda."
                )
            else:
                insight_mercado = (
                    f"Estás en la posición #{posicion_ocupacion} en ocupación y #{posicion_precio} en precio de tu zona ({len(filas)} clubes monitoreados). "
                    f"Tu posicionamiento actual es consistente con el mercado."
                )
        else:
            insight_mercado = "No hay suficientes datos de precio de tus vecinos esta semana para comparar posicionamiento."

        # ---------- S5: Ingreso estimado ----------
        cur.execute(
            "SELECT SUM(horas_totales) AS ht, SUM(horas_reservadas) AS hr FROM snapshots_ingreso "
            "WHERE tenant_id = %s AND fecha >= %s AND fecha < %s",
            (club_id, semana_inicio, semana_fin_exclusiva),
        )
        r = cur.fetchone()
        horas_totales = float(r["ht"]) if r and r["ht"] else 0.0
        horas_reservadas = float(r["hr"]) if r and r["hr"] else 0.0
        horas_vacias = max(horas_totales - horas_reservadas, 0)
        no_capturado = horas_vacias * (precio_club or 0)
        horas_para_75 = max(0.75 * horas_totales - horas_reservadas, 0)
        proyeccion_75 = horas_para_75 * (precio_club or 0)

        ing_delta_color = "var(--green)" if (ingreso_previo is not None and ingreso_actual > ingreso_previo) else ("var(--red)" if ingreso_previo is not None and ingreso_actual < ingreso_previo else "var(--muted)")
        ingreso = {
            "total": fmt_dinero(ingreso_actual), "delta_texto": ing_texto, "delta_color": ing_delta_color,
            "horas_vendidas": f"{horas_reservadas:.0f}", "horas_disponibles": f"{horas_totales:.0f}",
            "horas_vacias": f"{horas_vacias:.0f}", "no_capturado": fmt_dinero(no_capturado),
            "proyeccion_75": fmt_dinero(proyeccion_75),
        }

        # ---------- S6: Recomendación ----------
        candidatas = [f for f in franjas_out if f["_diff"] is not None and f["_diff"] < -10 and f["_ocup"] >= 60]
        if candidatas:
            peor = min(candidatas, key=lambda f: f["_diff"])
            nuevo_precio = peor["_precio_club"] + abs(peor["_diff"]) * 0.7
            impacto = abs(peor["_diff"]) * 0.7 * peor["_ocup"] / 100 * horas_totales / len(FRANJAS)
            reco = {
                "titulo": f"Sube tu precio de {peor['_nombre_corto'].lower()} y captura el ingreso que estás dejando en la mesa",
                "cuerpo": (
                    f"Tu franja de {peor['_nombre_corto'].lower()} tiene {peor['_ocup']:.0f}% de ocupación con ${peor['_precio_club']:.0f}/hr, "
                    f"mientras el promedio de tu zona es ${peor['_precio_zona']:.0f}/hr. Esa franja se llena de todas formas — no estás "
                    f"cobrando lo que el mercado ya acepta. Ajustar ese precio podría sumar del orden de <strong>${impacto:,.0f} adicionales</strong> por semana sin necesitar un solo cliente nuevo."
                ),
                "paso": f"Esta semana, ajusta el precio de la franja {peor['_nombre_corto'].lower()} de ${peor['_precio_club']:.0f} a ~${nuevo_precio:.0f}. Monitorea si la ocupación cae en el reporte de la próxima semana.",
            }
        elif franjas_bajas:
            reco = {
                "titulo": "Activa una promoción en tus horarios de menor demanda entre semana",
                "cuerpo": (
                    f"Detectamos {len(franjas_bajas)} franjas entre semana con ocupación menor al 20% "
                    f"({', '.join(franjas_bajas[:4])}{'…' if len(franjas_bajas) > 4 else ''}). Una cancha vacía no genera nada — "
                    f"un descuento en esos horarios puede convertir horas muertas en ingreso incremental."
                ),
                "paso": "Activa una promoción tipo \"Mediodía Activo\" en Playtomic para esas franjas específicas y compara la ocupación en el próximo reporte.",
            }
        else:
            reco = {
                "titulo": "Mantén el rumbo — tu ocupación y precios están alineados con el mercado",
                "cuerpo": "Esta semana no se detectó una oportunidad de precio o de ocupación crítica. Sigue monitoreando la evolución semana a semana para detectar cambios en la demanda de tu zona.",
                "paso": "Revisa el próximo reporte semanal para confirmar que la tendencia se mantiene.",
            }

        # ---------- Render ----------
        hoy = date.today()
        semana_fin_inclusiva = semana_fin_exclusiva - timedelta(days=1)
        semana_rango = f"{semana_inicio.day} – {semana_fin_inclusiva.day} de {MESES_ES[semana_fin_inclusiva.month]} {semana_fin_inclusiva.year}"
        fecha_generado = f"{hoy.day} de {MESES_ES[hoy.month]} de {hoy.year}"

        tpl = _env.get_template("semanal.html.j2")
        html = tpl.render(
            club=club, semana_rango=semana_rango, fecha_generado=fecha_generado,
            semana_numero=semana_inicio.isocalendar()[1], anio=semana_inicio.year,
            kpi_ocupacion=kpi_ocupacion, kpi_ingreso=kpi_ingreso, kpi_precio=kpi_precio,
            horas_headers=horas_headers, heatmap=heatmap, ocupacion_dias=ocupacion_dias,
            hora_pico=hora_pico, hora_valle=hora_valle, dias_sobre_70=dias_sobre_70,
            insight_ocupacion=insight_ocupacion, franjas=franjas_out, insight_precios=insight_precios,
            posicion_ocupacion=posicion_ocupacion, posicion_precio=posicion_precio,
            total_clubes_grupo=len(filas), comparativo=comparativo, promedio_zona=promedio_zona,
            insight_mercado=insight_mercado, ingreso=ingreso, reco=reco,
        )

        return html, club, semana_inicio
    finally:
        if conn_propia:
            conn.close()
