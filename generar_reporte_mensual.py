"""
Vaxta Edge — Generador de reporte mensual por club (línea de comandos)

Genera el HTML y lo guarda en reportes_generados/. Usa la misma lógica
que la ruta web en Render (ver reportes_mensual.py).

Uso:
  /usr/bin/python3 generar_reporte_mensual.py <tenant_id> [YYYY-MM]

  YYYY-MM es el mes a reportar. Si se omite, usa el último mes calendario
  completo (ej. si hoy es julio, usa junio).
"""

import os
import sys

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

from reportes_mensual import generar_html_mensual  # noqa: E402  (después de load_dotenv)


def generar(club_id, mes=None):
    try:
        html, club, mes_inicio = generar_html_mensual(club_id, mes)
    except ValueError as e:
        print(e)
        return None

    out_dir = os.path.join(HERE, "reportes_generados")
    os.makedirs(out_dir, exist_ok=True)
    safe_name = club["nombre"].lower().replace(" ", "_")
    out_path = os.path.join(out_dir, f"{safe_name}_mensual_{mes_inicio.strftime('%Y-%m')}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Reporte generado: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 generar_reporte_mensual.py <tenant_id> [YYYY-MM]")
        sys.exit(1)
    club_id_arg = sys.argv[1]
    mes_arg = sys.argv[2] if len(sys.argv) > 2 else None
    generar(club_id_arg, mes_arg)
