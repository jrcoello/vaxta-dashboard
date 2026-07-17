"""
Vaxta Edge — Backend del dashboard competitivo

Sirve la app estática (dashboard/static/) y expone una API que consulta
Supabase en vivo para cualquier club + cualquier semana, usando el usuario
de solo lectura (looker_readonly) — la contraseña vive aquí en el
servidor, nunca en el navegador. Todo el sitio está protegido con una
sola contraseña compartida (DASHBOARD_PASSWORD) vía HTTP Basic Auth.

Local:
  /usr/bin/python3 app.py
  Abre http://localhost:5001 en el navegador

En Render: las variables de entorno (LOOKER_READONLY_PASSWORD,
DASHBOARD_PASSWORD) se configuran en el panel de Render, no en este
archivo ni en un .env subido — ese .env es solo para desarrollo local
y está en .gitignore.
"""

import os
from flask import Flask, jsonify, send_from_directory, request, Response
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

from reportes_semanal import generar_html_semanal  # noqa: E402  (después de load_dotenv)
from reportes_mensual import generar_html_mensual  # noqa: E402
from courtmetrics import generar_html_courtmetrics  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")


@app.before_request
def proteger_todo():
    """Protege todo el sitio con una sola contraseña compartida (HTTP Basic Auth).
    Si no hay DASHBOARD_PASSWORD configurada (desarrollo local), no pide nada."""
    pw_esperado = os.environ.get("DASHBOARD_PASSWORD")
    if not pw_esperado:
        return
    auth = request.authorization
    if not auth or auth.password != pw_esperado:
        return Response(
            "Acceso restringido — Vaxta Edge",
            401,
            {"WWW-Authenticate": 'Basic realm="Vaxta Edge Dashboard"'},
        )


def conectar():
    """Conecta con el usuario de solo lectura — nunca con el de escritura."""
    pw = os.environ["LOOKER_READONLY_PASSWORD"]
    dsn = (
        f"postgresql://looker_readonly.foaryubaknxlkwxxndnn:{pw}"
        f"@aws-1-us-east-2.pooler.supabase.com:6543/postgres"
    )
    return psycopg2.connect(dsn)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/clubes")
def api_clubes():
    """Lista de los 594 clubes, para el buscador del selector."""
    conn = conectar()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT tenant_id AS id, nombre, mercado, canchas FROM clubs "
            "WHERE excluir_analisis = false ORDER BY nombre"
        )
        return jsonify(cur.fetchall())
    finally:
        conn.close()


@app.route("/api/semanas")
def api_semanas():
    """Semanas disponibles en todo el histórico (comparten fechas todos los
    clubes, así que no depende de cuál esté seleccionado)."""
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT semana_inicio FROM ingreso_semanal ORDER BY semana_inicio;")
        return jsonify([str(r[0]) for r in cur.fetchall()])
    finally:
        conn.close()


def obtener_grupo(cur, club_id):
    """Club + sus hasta 15 vecinos (10km). Devuelve (club, vecinos, ids_grupo).
    Clubes marcados excluir_analisis (privados, sin pricing real) no son
    seleccionables ni aunque se pida su id directo."""
    cur.execute(
        "SELECT tenant_id AS id, nombre, mercado, canchas, cerrado_temporalmente, cerrado_desde "
        "FROM clubs WHERE tenant_id = %s AND excluir_analisis = false",
        (club_id,),
    )
    club = cur.fetchone()
    if not club:
        return None, None, None
    if club.get("cerrado_desde"):
        club["cerrado_desde"] = str(club["cerrado_desde"])

    cur.execute(
        """
        SELECT c.tenant_id AS id, c.nombre, c.canchas, v.distancia_km, v.rank
        FROM club_vecinos v
        JOIN clubs c ON c.tenant_id = v.vecino_id
        WHERE v.club_id = %s
        ORDER BY v.rank
        """,
        (club_id,),
    )
    vecinos = cur.fetchall()
    ids_grupo = [club_id] + [v["id"] for v in vecinos]
    return club, vecinos, ids_grupo


@app.route("/api/comparacion/<club_id>")
def api_comparacion(club_id):
    """Toda la data para las 5 gráficas del club + sus vecinos, en la
    semana indicada por ?semana=YYYY-MM-DD (default: la más reciente)."""
    conn = conectar()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        club, vecinos, ids_grupo = obtener_grupo(cur, club_id)
        if club is None:
            return jsonify({"error": "Club no encontrado"}), 404

        semana = request.args.get("semana")
        if not semana:
            cur.execute("SELECT MAX(semana_inicio) FROM ingreso_semanal;")
            semana = str(cur.fetchone()["max"])

        # ---- 1) Cuadrante: precio/ocupación de la semana seleccionada ----
        cur.execute(
            """
            SELECT tenant_id AS id,
                   ROUND(AVG(precio_efectivo)::numeric, 0) AS precio_prom,
                   ROUND(MIN(precio_efectivo)::numeric, 0) AS precio_min,
                   ROUND(MAX(precio_efectivo)::numeric, 0) AS precio_max,
                   ROUND(AVG(ocupacion_pct)::numeric, 1) AS ocupacion_prom
            FROM snapshots_ingreso
            WHERE tenant_id = ANY(%s)
              AND fecha >= %s AND fecha < %s::date + INTERVAL '7 days'
              AND precio_efectivo IS NOT NULL
            GROUP BY tenant_id
            """,
            (ids_grupo, semana, semana),
        )
        cuadrante_por_id = {r["id"]: r for r in cur.fetchall()}

        def con_cuadrante(c):
            q = cuadrante_por_id.get(c["id"])
            return {**c, **q} if q else None

        club_cuad = con_cuadrante(club)
        vecinos_cuad = [v for v in (con_cuadrante(v) for v in vecinos) if v]
        sin_precio = [v["nombre"] for v in vecinos if v["id"] not in cuadrante_por_id]

        # ---- 2) Precio por hora, misma semana ----
        cur.execute(
            """
            SELECT tenant_id AS id, hora_inicio, ROUND(AVG(precio_efectivo)::numeric, 0) AS precio
            FROM snapshots_ingreso
            WHERE tenant_id = ANY(%s)
              AND fecha >= %s AND fecha < %s::date + INTERVAL '7 days'
              AND precio_efectivo IS NOT NULL
            GROUP BY tenant_id, hora_inicio
            ORDER BY tenant_id, hora_inicio
            """,
            (ids_grupo, semana, semana),
        )
        precio_hora_rows = cur.fetchall()
        precio_hora = {}
        for r in precio_hora_rows:
            precio_hora.setdefault(r["id"], []).append(
                {"hora": str(r["hora_inicio"])[:5], "precio": float(r["precio"])}
            )

        # ---- 2b) Ocupación por hora, misma semana (comparativo, todo el grupo) ----
        cur.execute(
            """
            SELECT tenant_id AS id, hora_inicio, ROUND(AVG(ocupacion_pct)::numeric, 1) AS ocupacion
            FROM snapshots_ingreso
            WHERE tenant_id = ANY(%s)
              AND fecha >= %s AND fecha < %s::date + INTERVAL '7 days'
            GROUP BY tenant_id, hora_inicio
            ORDER BY tenant_id, hora_inicio
            """,
            (ids_grupo, semana, semana),
        )
        ocupacion_hora_rows = cur.fetchall()
        ocupacion_hora = {}
        for r in ocupacion_hora_rows:
            ocupacion_hora.setdefault(r["id"], []).append(
                {"hora": str(r["hora_inicio"])[:5], "ocupacion": float(r["ocupacion"])}
            )

        # ---- 3) Ingreso esta semana + por cancha ----
        cur.execute(
            """
            SELECT tenant_id AS id, ingreso_estimado_mxn, ingreso_por_cancha_mxn
            FROM ingreso_semanal
            WHERE tenant_id = ANY(%s) AND semana_inicio = %s
            """,
            (ids_grupo, semana),
        )
        ingreso_semana = {r["id"]: r for r in cur.fetchall()}

        # ---- 4) Tendencia: histórico del grupo HASTA la semana seleccionada
        # (no más allá, aunque haya semanas más nuevas en la base), con
        # relleno de huecos conocidos (ver rellenar_huecos.sql) para que un
        # hueco de datos (ej. 6-7 mayo 2026 perdidos) no se vea como una
        # caída real de negocio. Esto es solo para esta gráfica de
        # contexto — "esta semana" y demás números puntuales siguen usando
        # ingreso_semanal real, sin rellenar.
        cur.execute(
            """
            SELECT tenant_id AS id, semana_inicio, ingreso_estimado_mxn, dias_estimados
            FROM ingreso_semanal_completo
            WHERE tenant_id = ANY(%s) AND semana_inicio <= %s
            ORDER BY semana_inicio
            """,
            (ids_grupo, semana),
        )
        tendencia_rows = cur.fetchall()
        for r in tendencia_rows:
            r["semana_inicio"] = str(r["semana_inicio"])  # evita el formato feo de Flask para fechas

        # ---- 5) Resumen del club solo: esta semana vs. la anterior ----
        cur.execute(
            """
            SELECT semana_inicio, ingreso_estimado_mxn, ingreso_por_cancha_mxn, ocupacion_prom_pct
            FROM ingreso_semanal
            WHERE tenant_id = %s AND semana_inicio IN (%s, %s::date - INTERVAL '7 days')
            """,
            (club_id, semana, semana),
        )
        resumen_por_semana = {}
        for r in cur.fetchall():
            r["semana_inicio"] = str(r["semana_inicio"])
            resumen_por_semana[r["semana_inicio"]] = r
        resumen_actual = resumen_por_semana.get(semana)
        semana_anterior = None
        for k in resumen_por_semana:
            if k != semana:
                semana_anterior = resumen_por_semana[k]

        # ---- 6) Ingreso y ocupación diaria del club, esta semana (Lun-Dom) ----
        cur.execute(
            """
            SELECT fecha, EXTRACT(ISODOW FROM fecha)::int AS dia_semana,
                   ingreso_estimado_mxn, ocupacion_prom_pct
            FROM ingreso_diario
            WHERE tenant_id = %s AND fecha >= %s AND fecha < %s::date + INTERVAL '7 days'
            ORDER BY fecha
            """,
            (club_id, semana, semana),
        )
        dias = cur.fetchall()
        for d in dias:
            d["fecha"] = str(d["fecha"])

        # ---- 7) Ocupación por hora del club, esta semana ----
        cur.execute(
            """
            SELECT hora_inicio, ROUND(AVG(ocupacion_pct)::numeric, 1) AS ocupacion_prom
            FROM snapshots_ingreso
            WHERE tenant_id = %s AND fecha >= %s AND fecha < %s::date + INTERVAL '7 days'
            GROUP BY hora_inicio
            ORDER BY hora_inicio
            """,
            (club_id, semana, semana),
        )
        horas_ocupacion = [
            {"hora": str(r["hora_inicio"])[:5], "ocupacion": float(r["ocupacion_prom"])}
            for r in cur.fetchall()
        ]

        nombres = {club["id"]: club["nombre"]}
        nombres.update({v["id"]: v["nombre"] for v in vecinos})

        return jsonify(
            {
                "semana": semana,
                "club": club,
                "vecinos": vecinos,
                "nombres": nombres,
                "cuadrante": {
                    "club": club_cuad,
                    "vecinos": vecinos_cuad,
                    "sin_precio": ([club["nombre"]] if club_cuad is None else []) + sin_precio,
                },
                "precio_hora": precio_hora,
                "ocupacion_hora": ocupacion_hora,
                "ingreso_semana": ingreso_semana,
                "tendencia_rows": tendencia_rows,
                "resumen_actual": resumen_actual,
                "resumen_anterior": semana_anterior,
                "dias": dias,
                "horas_ocupacion": horas_ocupacion,
            }
        )
    finally:
        conn.close()


@app.route("/reportes")
def reportes_selector():
    """Página simple para elegir un club y abrir su reporte semanal
    (reusa el mismo buscador de clubes que ya existe en /api/clubes)."""
    return send_from_directory(app.static_folder, "reportes.html")


@app.route("/reporte/semanal/<club_id>")
def reporte_semanal(club_id):
    """Genera y sirve el reporte semanal de cualquier club en vivo, para
    poder mandar el link directo (?semana=YYYY-MM-DD opcional, default =
    la semana más reciente con datos)."""
    semana = request.args.get("semana")
    try:
        html, club, semana_usada = generar_html_semanal(club_id, semana)
    except ValueError as e:
        return f"<p style='font-family:sans-serif;padding:40px'>{e}</p>", 404
    return Response(html, mimetype="text/html")


@app.route("/reporte/mensual/<club_id>")
def reporte_mensual(club_id):
    """Genera y sirve el reporte mensual de cualquier club en vivo
    (?mes=YYYY-MM opcional, default = el último mes calendario completo)."""
    mes = request.args.get("mes")
    try:
        html, club, mes_usado = generar_html_mensual(club_id, mes)
    except ValueError as e:
        return f"<p style='font-family:sans-serif;padding:40px'>{e}</p>", 404
    return Response(html, mimetype="text/html")


@app.route("/courtmetrics")
def courtmetrics():
    """Página de Revés Padel con datos de Court Metrics — captura manual
    guardada en courtmetrics_reves.json, no consulta Supabase ni ningún
    servicio en vivo (ver courtmetrics.py)."""
    return Response(generar_html_courtmetrics(), mimetype="text/html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
