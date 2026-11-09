from app import servicio
from app.db import conectar, iniciar_db

GRUPOS = ["6A", "7A", "8A", "9A", "10A", "11A"]
SALONES = ["101", "102", "103", "104", "105", "106", "Aula de sistemas"]

conn = conectar()
iniciar_db(conn)

for tabla, nombres in (("grupos", GRUPOS), ("salones", SALONES)):
    for nombre in nombres:
        try:
            servicio.crear_catalogo(conn, tabla, nombre)
            print(f"{tabla}: {nombre} creado")
        except servicio.Conflicto:
            print(f"{tabla}: {nombre} ya existía")

conn.close()