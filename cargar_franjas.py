from app import servicio
from app.db import conectar, iniciar_db

# (orden, inicio, fin) en formato de 24 horas
FRANJAS = [
    (1, "06:30", "07:25"),
    (2, "07:25", "08:20"),
    # descanso 08:20 - 08:40
    (3, "08:40", "09:35"),
    (4, "09:35", "10:30"),
    # descanso 10:30 - 10:50
    (5, "10:50", "11:45"),
    (6, "11:45", "12:40"),
    (7, "12:40", "13:25"),
]

conn = conectar()
iniciar_db(conn)

for orden, inicio, fin in FRANJAS:
    try:
        servicio.crear_franja(conn, orden, inicio, fin)
        print(f"Franja {orden}: {inicio} - {fin} creada")
    except servicio.ErrorDeNegocio as e:
        print(f"Franja {orden}: omitida ({e})")

conn.close()