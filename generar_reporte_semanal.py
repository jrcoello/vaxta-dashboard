"""
Vaxta Edge — Generador de reporte semanal por club (línea de comandos)

Genera el HTML y lo guarda en reportes_generados/. Usa la misma lógica
que la ruta web en Render (ver reportes_semanal.py) — útil para revisar
un reporte localmente antes de mandarlo, o para tenerlo como archivo.

Uso:
  /usr/bin/python3 generar_reporte_semanal.py <tenant_id> [YYYY-MM-DD]

  YYYY-MM-DD es el lunes de la semana a reportar. Si se omite, usa la
  semana más reciente disponible para ese club en ingreso_semanal.
"""

import os
import sys

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

from reportes_semanal import generar_html_semanal  # noqa: E402  (después de load_dotenv)


def generar(club_id, semana_inicio=None):
    try:
        html, club, semana = generar_html_semanal(club_id, semana_inicio)
    except ValueError as e:
        print(e)
        return None

    out_dir = os.path.join(HERE, "reportes_generados")
    os.makedirs(out_dir, exist_ok=True)
    safe_name = club["nombre"].lower().replace(" ", "_")
    out_path = os.path.join(out_dir, f"{safe_name}_semanal_{semana.isoformat()}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Reporte generado: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 generar_reporte_semanal.py <tenant_id> [YYYY-MM-DD]")
        sys.exit(1)
    club_id_arg = sys.argv[1]
    semana_arg = sys.argv[2] if len(sys.argv) > 2 else None
    generar(club_id_arg, semana_arg)
