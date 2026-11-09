"""Lógica de negocio del horario. No depende de FastAPI, así se puede probar sola."""
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta

CATALOGOS = {"profesores", "materias", "grupos", "salones"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes"]
FORMATO_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ErrorDeNegocio(Exception):
    """Datos inválidos (400)."""


class NoEncontrado(ErrorDeNegocio):
    """El registro no existe (404)."""


class Conflicto(ErrorDeNegocio):
    """Choque de horario o duplicado (409)."""


# ---------- Catálogos: profesores, materias, grupos, salones ----------

def _validar_tabla(tabla):
    if tabla not in CATALOGOS:
        raise NoEncontrado(f"Catálogo desconocido: {tabla}")


def crear_catalogo(conn, tabla, nombre):
    _validar_tabla(tabla)
    nombre = (nombre or "").strip()
    if not nombre:
        raise ErrorDeNegocio("El nombre no puede estar vacío")
    try:
        cur = conn.execute(f"INSERT INTO {tabla} (nombre) VALUES (?)", (nombre,))
    except sqlite3.IntegrityError:
        raise Conflicto(f"Ya existe '{nombre}' en {tabla}")
    conn.commit()
    return {"id": cur.lastrowid, "nombre": nombre}


def _clave_natural(texto):
    """Ordena 6A, 7A... 10A, 11A como números, e ignora tildes y mayúsculas."""
    texto = "".join(c for c in unicodedata.normalize("NFD", texto)
                    if unicodedata.category(c) != "Mn")
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", texto)]


def listar_catalogo(conn, tabla):
    _validar_tabla(tabla)
    filas = conn.execute(f"SELECT id, nombre FROM {tabla}").fetchall()
    return sorted((dict(f) for f in filas), key=lambda f: _clave_natural(f["nombre"]))


def eliminar_registro(conn, tabla, id_):
    """Elimina un profesor, materia, grupo, salón o franja que no esté en uso."""
    if tabla not in CATALOGOS | {"franjas"}:
        raise NoEncontrado(f"Tabla desconocida: {tabla}")
    try:
        if tabla == "profesores":
            conn.execute("DELETE FROM usuarios WHERE profesor_id = ?", (id_,))
            conn.execute("DELETE FROM profesor_materias WHERE profesor_id = ?", (id_,))
        elif tabla == "materias":
            conn.execute("DELETE FROM profesor_materias WHERE materia_id = ?", (id_,))
            conn.execute("DELETE FROM salon_materias WHERE materia_id = ?", (id_,))
        elif tabla == "salones":
            conn.execute("DELETE FROM salon_materias WHERE salon_id = ?", (id_,))
            conn.execute("DELETE FROM grupo_salon WHERE salon_id = ?", (id_,))
        conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (id_,))
    except sqlite3.IntegrityError:
        conn.rollback()
        raise Conflicto("No se puede eliminar: está en uso (clases, ausencias o reemplazos asignados)")
    conn.commit()



def crear_aula(conn, nombre, tipo):
    tipo = (tipo or "").strip().lower()
    if tipo not in ("principal", "especial"):
        raise ErrorDeNegocio("Selecciona si el aula es principal o especial")
    nombre = (nombre or "").strip()
    if not nombre:
        raise ErrorDeNegocio("El nombre del aula no puede estar vacío")
    try:
        cur = conn.execute("INSERT INTO salones(nombre,tipo) VALUES(?,?)", (nombre, tipo))
    except sqlite3.IntegrityError:
        raise Conflicto(f"Ya existe un aula llamada '{nombre}'")
    conn.commit()
    return {"id": cur.lastrowid, "nombre": nombre, "tipo": tipo}


def cambiar_tipo_aula(conn, salon_id, tipo):
    """Cambia un aula entre principal y especial, limpiando asignaciones que ya no aplican."""
    _existe(conn, "salones", salon_id, "el aula")
    if tipo not in ("principal", "especial"):
        raise ErrorDeNegocio("Selecciona si el aula es principal o especial")
    if tipo == "especial":
        conn.execute("DELETE FROM grupo_salon WHERE salon_id = ?", (salon_id,))
    else:
        conn.execute("DELETE FROM salon_materias WHERE salon_id = ?", (salon_id,))
    conn.execute("UPDATE salones SET tipo = ? WHERE id = ?", (tipo, salon_id))
    conn.commit()


def listar_aulas(conn, tipo=None):
    if tipo:
        filas = conn.execute("SELECT id,nombre,tipo FROM salones WHERE tipo=? ORDER BY nombre", (tipo,))
    else:
        filas = conn.execute("SELECT id,nombre,tipo FROM salones ORDER BY tipo,nombre")
    return sorted((dict(r) for r in filas), key=lambda a: (a["tipo"], _clave_natural(a["nombre"])))


def asignar_salon_grupo(conn, grupo_id, salon_id):
    _existe(conn, "grupos", grupo_id, "el grupo")
    _existe(conn, "salones", salon_id, "el aula")
    aula = conn.execute("SELECT tipo FROM salones WHERE id=?", (salon_id,)).fetchone()
    if aula["tipo"] != "principal":
        raise ErrorDeNegocio("Un grupo solo puede tener asignada un aula principal")
    ocupacion = conn.execute(
        "SELECT g.nombre FROM grupo_salon gs "
        "JOIN grupos g ON g.id=gs.grupo_id "
        "WHERE gs.salon_id=? AND gs.grupo_id<>? LIMIT 1",
        (salon_id, grupo_id),
    ).fetchone()
    if ocupacion:
        raise ErrorDeNegocio(
            f"El aula principal {conn.execute('SELECT nombre FROM salones WHERE id=?', (salon_id,)).fetchone()['nombre']} "
            f"ya está asignada al grupo {ocupacion['nombre']}. "
            "Cada grupo debe tener un aula principal diferente."
        )
    conn.execute("INSERT INTO grupo_salon(grupo_id,salon_id) VALUES(?,?) "
                 "ON CONFLICT(grupo_id) DO UPDATE SET salon_id=excluded.salon_id",
                 (grupo_id, salon_id))
    conn.commit()


def asignar_materias_salon(conn, salon_id, materia_ids):
    _existe(conn, "salones", salon_id, "el aula")
    aula = conn.execute("SELECT tipo FROM salones WHERE id=?", (salon_id,)).fetchone()
    if aula["tipo"] != "especial":
        raise ErrorDeNegocio("Solo las aulas especiales pueden asociarse con materias")
    ids = set(materia_ids)
    for materia_id in ids:
        _existe(conn, "materias", materia_id, "la materia")
    conn.execute("DELETE FROM salon_materias WHERE salon_id=?", (salon_id,))
    conn.executemany("INSERT INTO salon_materias(salon_id,materia_id) VALUES(?,?)",
                     [(salon_id, m) for m in ids])
    conn.commit()


def asignaciones_aulas(conn):
    grupos = [dict(r) for r in conn.execute(
        "SELECT g.id grupo_id,g.nombre grupo,s.id salon_id,s.nombre salon "
        "FROM grupos g LEFT JOIN grupo_salon gs ON gs.grupo_id=g.id "
        "LEFT JOIN salones s ON s.id=gs.salon_id")]
    grupos.sort(key=lambda g: _clave_natural(g["grupo"]))
    especiales = {}
    for r in conn.execute("SELECT sm.salon_id,s.nombre salon,m.id materia_id,m.nombre materia "
                          "FROM salon_materias sm JOIN salones s ON s.id=sm.salon_id "
                          "JOIN materias m ON m.id=sm.materia_id ORDER BY s.nombre,m.nombre"):
        especiales.setdefault(r["salon_id"], []).append({"id": r["materia_id"], "nombre": r["materia"]})
    return grupos, especiales


def salon_requerido(conn, grupo_id, materia_id):
    especial = conn.execute("SELECT salon_id FROM salon_materias WHERE materia_id=? ORDER BY salon_id LIMIT 1",
                            (materia_id,)).fetchone()
    if especial:
        return especial["salon_id"]
    base = conn.execute("SELECT salon_id FROM grupo_salon WHERE grupo_id=?", (grupo_id,)).fetchone()
    return base["salon_id"] if base else None

def asignar_materias(conn, profesor_id, materia_ids):
    """Define (reemplazando) las materias que dicta un profesor."""
    _existe(conn, "profesores", profesor_id, "el profesor")
    ids = set(materia_ids)
    for m in ids:
        _existe(conn, "materias", m, "la materia")
    conn.execute("DELETE FROM profesor_materias WHERE profesor_id = ?", (profesor_id,))
    conn.executemany(
        "INSERT INTO profesor_materias (profesor_id, materia_id) VALUES (?, ?)",
        [(profesor_id, m) for m in ids],
    )
    conn.commit()


def materias_por_profesor(conn):
    """{profesor_id: [{'id': ..., 'nombre': ...}, ...]}"""
    resultado = {}
    for f in conn.execute(
        "SELECT pm.profesor_id, m.id, m.nombre FROM profesor_materias pm "
        "JOIN materias m ON m.id = pm.materia_id ORDER BY m.nombre"
    ):
        resultado.setdefault(f["profesor_id"], []).append(
            {"id": f["id"], "nombre": f["nombre"]})
    for lista in resultado.values():
        lista.sort(key=lambda m: _clave_natural(m["nombre"]))
    return resultado


# ---------- Franjas horarias ----------

TIPOS_FRANJA = ("clase", "descanso")


def _validar_franja(conn, inicio, fin, tipo, excluir_id=None):
    if not (FORMATO_HORA.match(inicio or "") and FORMATO_HORA.match(fin or "")):
        raise ErrorDeNegocio("Las horas deben tener formato HH:MM")
    if inicio >= fin:
        raise ErrorDeNegocio("La hora de inicio debe ser menor que la de fin")
    if tipo not in TIPOS_FRANJA:
        raise ErrorDeNegocio("El tipo de franja debe ser clase o descanso")
    cruce = conn.execute(
        "SELECT inicio, fin FROM franjas WHERE id != ? AND inicio < ? AND ? < fin",
        (excluir_id if excluir_id is not None else -1, fin, inicio),
    ).fetchone()
    if cruce:
        raise Conflicto(f"Se cruza con la franja {cruce['inicio']} - {cruce['fin']}")


def crear_franja(conn, orden, inicio, fin, tipo="clase"):
    """Crea una franja de clase o de descanso. Si orden es None se asigna sola."""
    _validar_franja(conn, inicio, fin, tipo)
    if orden is None:
        orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM franjas").fetchone()[0]
    try:
        cur = conn.execute(
            "INSERT INTO franjas (orden, inicio, fin, tipo) VALUES (?, ?, ?, ?)",
            (orden, inicio, fin, tipo),
        )
    except sqlite3.IntegrityError:
        raise Conflicto(f"Ya existe una franja con orden {orden}")
    conn.commit()
    return {"id": cur.lastrowid, "orden": orden, "inicio": inicio, "fin": fin, "tipo": tipo}


def actualizar_franja(conn, franja_id, orden, inicio, fin, tipo="clase"):
    actual = conn.execute("SELECT * FROM franjas WHERE id = ?", (franja_id,)).fetchone()
    if actual is None:
        raise NoEncontrado(f"No existe la franja con id {franja_id}")
    _validar_franja(conn, inicio, fin, tipo, excluir_id=franja_id)
    if tipo == "descanso" and conn.execute(
            "SELECT 1 FROM clases WHERE franja_id = ?", (franja_id,)).fetchone():
        raise Conflicto("No puede ser descanso: tiene clases asignadas. Muévelas o elimínalas primero")
    try:
        conn.execute(
            "UPDATE franjas SET orden = ?, inicio = ?, fin = ?, tipo = ? WHERE id = ?",
            (actual["orden"] if orden is None else orden, inicio, fin, tipo, franja_id),
        )
    except sqlite3.IntegrityError:
        raise Conflicto(f"Ya existe una franja con orden {orden}")
    conn.commit()


def listar_franjas(conn):
    """Franjas ordenadas por hora de inicio (incluye los descansos)."""
    filas = conn.execute("SELECT * FROM franjas ORDER BY inicio").fetchall()
    return [dict(f) for f in filas]


# ---------- Clases ----------

_SELECT_CLASES = """
SELECT c.id, c.dia, c.franja_id, f.inicio, f.fin,
       m.id AS materia_id, m.nombre AS materia,
       p.id AS profesor_id, p.nombre AS profesor,
       g.id AS grupo_id, g.nombre AS grupo,
       s.id AS salon_id, s.nombre AS salon
FROM clases c
JOIN franjas f ON f.id = c.franja_id
JOIN materias m ON m.id = c.materia_id
JOIN profesores p ON p.id = c.profesor_id
JOIN grupos g ON g.id = c.grupo_id
JOIN salones s ON s.id = c.salon_id
"""


def _existe(conn, tabla, id_, etiqueta):
    if conn.execute(f"SELECT 1 FROM {tabla} WHERE id = ?", (id_,)).fetchone() is None:
        raise NoEncontrado(f"No existe {etiqueta} con id {id_}")


def _verificar(conn, materia_id, profesor_id, grupo_id, salon_id, dia, franja_id,
               excluir_id=None):
    """Valida que existan las referencias y que no haya choques de horario."""
    if dia not in range(5):
        raise ErrorDeNegocio("El día debe ser un número de 0 (lunes) a 4 (viernes)")
    _existe(conn, "materias", materia_id, "la materia")
    _existe(conn, "profesores", profesor_id, "el profesor")
    _existe(conn, "grupos", grupo_id, "el grupo")
    _existe(conn, "salones", salon_id, "el salón")
    _existe(conn, "franjas", franja_id, "la franja")
    if conn.execute("SELECT tipo FROM franjas WHERE id = ?", (franja_id,)).fetchone()["tipo"] == "descanso":
        raise ErrorDeNegocio("No se pueden programar clases en una franja de descanso")
    aulas_especiales = [r["salon_id"] for r in conn.execute(
        "SELECT salon_id FROM salon_materias WHERE materia_id=?", (materia_id,))]
    if aulas_especiales:
        if salon_id not in aulas_especiales:
            raise ErrorDeNegocio("Esta materia debe dictarse en un aula especializada asignada a ella")
    else:
        salon_asignado = salon_requerido(conn, grupo_id, materia_id)
        if salon_asignado is not None and salon_asignado != salon_id:
            raise ErrorDeNegocio("Esta materia debe dictarse en el aula principal asignada al grupo")
    if conn.execute(
        "SELECT 1 FROM profesor_materias WHERE profesor_id = ? AND materia_id = ?",
        (profesor_id, materia_id),
    ).fetchone() is None:
        raise ErrorDeNegocio(
            "Ese profesor no tiene asignada esa materia (asígnasela en Profesores)")

    reglas = [
        ("profesor_id", profesor_id, "El profesor ya tiene clase"),
        ("grupo_id", grupo_id, "El grupo ya tiene clase"),
        ("salon_id", salon_id, "El salón ya está ocupado"),
    ]
    for campo, valor, mensaje in reglas:
        choque = conn.execute(
            f"SELECT id FROM clases WHERE dia = ? AND franja_id = ? AND {campo} = ? "
            "AND id != ?",
            (dia, franja_id, valor, excluir_id if excluir_id is not None else -1),
        ).fetchone()
        if choque:
            raise Conflicto(f"{mensaje} en ese día y franja (clase #{choque['id']})")


def obtener_clase(conn, clase_id):
    fila = conn.execute(_SELECT_CLASES + " WHERE c.id = ?", (clase_id,)).fetchone()
    if fila is None:
        raise NoEncontrado(f"No existe la clase {clase_id}")
    return dict(fila)


def crear_clase(conn, materia_id, profesor_id, grupo_id, salon_id, dia, franja_id):
    _verificar(conn, materia_id, profesor_id, grupo_id, salon_id, dia, franja_id)
    cur = conn.execute(
        "INSERT INTO clases (materia_id, profesor_id, grupo_id, salon_id, dia, franja_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (materia_id, profesor_id, grupo_id, salon_id, dia, franja_id),
    )
    conn.commit()
    return obtener_clase(conn, cur.lastrowid)


def actualizar_clase(conn, clase_id, materia_id, profesor_id, grupo_id, salon_id,
                     dia, franja_id):
    obtener_clase(conn, clase_id)  # lanza NoEncontrado si no existe
    _verificar(conn, materia_id, profesor_id, grupo_id, salon_id, dia, franja_id,
               excluir_id=clase_id)
    conn.execute(
        "UPDATE clases SET materia_id=?, profesor_id=?, grupo_id=?, salon_id=?, "
        "dia=?, franja_id=? WHERE id=?",
        (materia_id, profesor_id, grupo_id, salon_id, dia, franja_id, clase_id),
    )
    conn.commit()
    return obtener_clase(conn, clase_id)


def eliminar_clase(conn, clase_id):
    obtener_clase(conn, clase_id)
    conn.execute("DELETE FROM clases WHERE id = ?", (clase_id,))
    conn.commit()


def listar_clases(conn, grupo_id=None, profesor_id=None, salon_id=None, dia=None):
    filtros, params = [], []
    for columna, valor in (("c.grupo_id", grupo_id), ("c.profesor_id", profesor_id),
                           ("c.salon_id", salon_id), ("c.dia", dia)):
        if valor is not None:
            filtros.append(f"{columna} = ?")
            params.append(valor)
    where = (" WHERE " + " AND ".join(filtros)) if filtros else ""
    filas = conn.execute(
        _SELECT_CLASES + where + " ORDER BY c.dia, f.inicio", params
    ).fetchall()
    return [dict(f) for f in filas]


# ---------- Consultas ----------

def profesores_libres(conn, dia, franja_id, fecha=None):
    """Profesores sin clase en un día y franja dados.

    Si se indica una fecha (AAAA-MM-DD), no cuenta a los profesores ausentes ese
    día ni a los que ya están cubriendo el reemplazo de otra clase en esa franja.
    """
    if dia not in range(5):
        raise ErrorDeNegocio("El día debe ser un número de 0 (lunes) a 4 (viernes)")
    _existe(conn, "franjas", franja_id, "la franja")
    if conn.execute("SELECT tipo FROM franjas WHERE id = ?", (franja_id,)).fetchone()["tipo"] == "descanso":
        return []
    consulta = ("SELECT id, nombre FROM profesores WHERE id NOT IN "
                "(SELECT profesor_id FROM clases WHERE dia = ? AND franja_id = ?)")
    params = [dia, franja_id]
    if fecha:
        consulta += (" AND id NOT IN (SELECT profesor_id FROM ausencias WHERE fecha = ?)"
                     " AND id NOT IN (SELECT r.profesor_reemplazo_id FROM reemplazos r "
                     "JOIN ausencias a ON a.id = r.ausencia_id JOIN clases c ON c.id = r.clase_id "
                     "WHERE a.fecha = ? AND c.franja_id = ?)")
        params += [fecha, fecha, franja_id]
    filas = conn.execute(consulta + " ORDER BY nombre", params).fetchall()
    return sorted((dict(f) for f in filas), key=lambda f: _clave_natural(f["nombre"]))


def estado_ahora(conn, momento=None):
    """Qué profesores están en clase y cuáles están libres en este momento.

    Tiene en cuenta los descansos, las ausencias del día y sus reemplazos.
    """
    momento = momento or datetime.now()
    dia = momento.weekday()
    hora = momento.strftime("%H:%M")
    vacio = {"momento": momento.isoformat(timespec="minutes"), "franja": None,
             "descanso": False, "en_clase": [], "libres": []}
    if dia > 4:
        return vacio
    franja = conn.execute(
        "SELECT * FROM franjas WHERE inicio <= ? AND ? < fin", (hora, hora)
    ).fetchone()
    if franja is None:
        return vacio
    if franja["tipo"] == "descanso":
        return {**vacio, "franja": dict(franja), "descanso": True}
    fecha = momento.date().isoformat()
    en_clase = [dict(f) for f in conn.execute(
        _SELECT_CLASES + " WHERE c.dia = ? AND c.franja_id = ? ORDER BY p.nombre",
        (dia, franja["id"]),
    )]
    ausentes = {r["profesor_id"] for r in conn.execute(
        "SELECT profesor_id FROM ausencias WHERE fecha = ?", (fecha,))}
    cubiertos = {r["clase_id"]: r["nombre"] for r in conn.execute(
        "SELECT r.clase_id, p.nombre FROM reemplazos r JOIN ausencias a ON a.id = r.ausencia_id "
        "JOIN profesores p ON p.id = r.profesor_reemplazo_id WHERE a.fecha = ?", (fecha,))}
    for c in en_clase:
        c["ausente"] = c["profesor_id"] in ausentes
        c["reemplazo"] = cubiertos.get(c["id"])
    return {
        "momento": vacio["momento"],
        "franja": dict(franja),
        "descanso": False,
        "en_clase": en_clase,
        "libres": profesores_libres(conn, dia, franja["id"], fecha),
    }


# ---------- Ausencias y reemplazos ----------

def registrar_ausencia(conn, profesor_id, desde, hasta, motivo, observaciones=""):
    """Registra una ausencia por cada día hábil entre dos fechas. Devuelve cuántos días."""
    _existe(conn, "profesores", profesor_id, "el profesor")
    try:
        d1 = date.fromisoformat(desde)
        d2 = date.fromisoformat(hasta or desde)
    except (TypeError, ValueError):
        raise ErrorDeNegocio("Las fechas no son válidas")
    if d2 < d1:
        raise ErrorDeNegocio("La fecha final no puede ser anterior a la inicial")
    if (d2 - d1).days > 90:
        raise ErrorDeNegocio("El rango no puede superar los 90 días")
    motivo = (motivo or "").strip() or "Otro"
    creadas, dia = 0, d1
    while dia <= d2:
        if dia.weekday() < 5:
            try:
                conn.execute(
                    "INSERT INTO ausencias (profesor_id, fecha, motivo, observaciones) "
                    "VALUES (?, ?, ?, ?)", (profesor_id, dia.isoformat(), motivo, observaciones or ""))
                creadas += 1
            except sqlite3.IntegrityError:
                pass  # ya había una ausencia ese día
        dia += timedelta(days=1)
    if not creadas:
        conn.rollback()
        raise ErrorDeNegocio("No se registró nada: eran fines de semana o ya existía la ausencia")
    conn.commit()
    return creadas


def eliminar_ausencia(conn, ausencia_id):
    cur = conn.execute("DELETE FROM ausencias WHERE id = ?", (ausencia_id,))
    if not cur.rowcount:
        raise NoEncontrado("No existe esa ausencia")
    conn.commit()


def _candidatos(conn, fecha, dia, franja_id, materia_id):
    """Profesores libres para cubrir; primero los que dictan esa materia."""
    ensenan = {r[0] for r in conn.execute(
        "SELECT profesor_id FROM profesor_materias WHERE materia_id = ?", (materia_id,))}
    lista = [{"id": p["id"], "nombre": p["nombre"], "ensenia": p["id"] in ensenan}
             for p in profesores_libres(conn, dia, franja_id, fecha)]
    lista.sort(key=lambda p: (not p["ensenia"], _clave_natural(p["nombre"])))
    return lista


def ausencias_con_clases(conn, desde=None):
    """Ausencias desde una fecha (hoy por defecto) con sus clases, reemplazos y candidatos."""
    desde = desde or date.today().isoformat()
    resultado = []
    for a in conn.execute(
            "SELECT a.*, p.nombre profesor FROM ausencias a JOIN profesores p ON p.id = a.profesor_id "
            "WHERE a.fecha >= ? ORDER BY a.fecha, p.nombre", (desde,)).fetchall():
        a = dict(a)
        dia = date.fromisoformat(a["fecha"]).weekday()
        clases = listar_clases(conn, profesor_id=a["profesor_id"], dia=dia) if dia < 5 else []
        cubiertas = {r["clase_id"]: dict(r) for r in conn.execute(
            "SELECT r.id, r.clase_id, p.nombre reemplazo FROM reemplazos r "
            "JOIN profesores p ON p.id = r.profesor_reemplazo_id WHERE r.ausencia_id = ?", (a["id"],))}
        for c in clases:
            c["reemplazo"] = cubiertas.get(c["id"])
            c["candidatos"] = _candidatos(conn, a["fecha"], dia, c["franja_id"], c["materia_id"])
        a["clases"] = clases
        a["sin_cubrir"] = sum(1 for c in clases if not c["reemplazo"])
        resultado.append(a)
    return resultado


def ausencias_pasadas(conn, limite=30):
    hoy = date.today().isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT a.*, p.nombre profesor FROM ausencias a JOIN profesores p ON p.id = a.profesor_id "
        "WHERE a.fecha < ? ORDER BY a.fecha DESC LIMIT ?", (hoy, limite))]


def asignar_reemplazo(conn, ausencia_id, clase_id, sustituto_id):
    """Asigna a un profesor libre para cubrir una clase de un profesor ausente."""
    a = conn.execute("SELECT * FROM ausencias WHERE id = ?", (ausencia_id,)).fetchone()
    if a is None:
        raise NoEncontrado("No existe esa ausencia")
    clase = obtener_clase(conn, clase_id)
    _existe(conn, "profesores", sustituto_id, "el profesor sustituto")
    if clase["profesor_id"] != a["profesor_id"]:
        raise ErrorDeNegocio("La clase no corresponde al profesor ausente")
    if date.fromisoformat(a["fecha"]).weekday() != clase["dia"]:
        raise ErrorDeNegocio("La clase no corresponde al día de la ausencia")
    if sustituto_id == a["profesor_id"]:
        raise ErrorDeNegocio("El reemplazo debe ser otro profesor")
    libres = {p["id"] for p in profesores_libres(conn, clase["dia"], clase["franja_id"], a["fecha"])}
    if sustituto_id not in libres:
        raise Conflicto("Ese profesor no está libre en esa franja (tiene clase, está ausente "
                        "o ya cubre otra clase)")
    conn.execute(
        "INSERT INTO reemplazos (ausencia_id, clase_id, profesor_reemplazo_id) VALUES (?, ?, ?) "
        "ON CONFLICT(clase_id, ausencia_id) DO UPDATE SET "
        "profesor_reemplazo_id = excluded.profesor_reemplazo_id",
        (ausencia_id, clase_id, sustituto_id))
    conn.commit()


def quitar_reemplazo(conn, reemplazo_id):
    cur = conn.execute("DELETE FROM reemplazos WHERE id = ?", (reemplazo_id,))
    if not cur.rowcount:
        raise NoEncontrado("No existe ese reemplazo")
    conn.commit()


# ---------- Cargas horarias y generación automática ----------

def guardar_carga(conn, grupo_id, materia_id, horas):
    _existe(conn, "grupos", grupo_id, "el grupo")
    _existe(conn, "materias", materia_id, "la materia")
    if horas not in range(36):
        raise ErrorDeNegocio("Las horas semanales deben estar entre 0 y 35")
    conn.execute(
        "INSERT INTO cargas_horarias (grupo_id, materia_id, horas) VALUES (?, ?, ?) "
        "ON CONFLICT(grupo_id, materia_id) DO UPDATE SET horas=excluded.horas",
        (grupo_id, materia_id, horas),
    )
    conn.commit()


def eliminar_carga(conn, grupo_id, materia_id):
    conn.execute(
        "DELETE FROM cargas_horarias WHERE grupo_id=? AND materia_id=?",
        (grupo_id, materia_id),
    )
    conn.commit()


def listar_cargas(conn):
    return [dict(r) for r in conn.execute(
        "SELECT ch.grupo_id, ch.materia_id, ch.horas, g.nombre grupo, m.nombre materia "
        "FROM cargas_horarias ch JOIN grupos g ON g.id=ch.grupo_id "
        "JOIN materias m ON m.id=ch.materia_id ORDER BY g.nombre, m.nombre")]


def estadisticas(conn):
    profesores = conn.execute("SELECT COUNT(*) n FROM profesores").fetchone()["n"]
    clases = conn.execute("SELECT COUNT(*) n FROM clases").fetchone()["n"]
    salones = conn.execute("SELECT COUNT(*) n FROM salones").fetchone()["n"]
    franjas = conn.execute("SELECT COUNT(*) n FROM franjas").fetchone()["n"]
    estado = estado_ahora(conn)
    ocupados = {c["salon_id"] for c in estado["en_clase"]}
    pendientes = conn.execute(
        "SELECT COUNT(*) n FROM cargas_horarias ch LEFT JOIN "
        "(SELECT grupo_id,materia_id,COUNT(*) total FROM clases GROUP BY grupo_id,materia_id) x "
        "ON x.grupo_id=ch.grupo_id AND x.materia_id=ch.materia_id "
        "WHERE ch.horas > COALESCE(x.total,0)").fetchone()["n"]
    pendientes_horas = conn.execute(
        "SELECT COALESCE(SUM(MAX(ch.horas - COALESCE(x.total,0),0)),0) n "
        "FROM cargas_horarias ch LEFT JOIN (SELECT grupo_id,materia_id,COUNT(*) total "
        "FROM clases GROUP BY grupo_id,materia_id) x "
        "ON x.grupo_id=ch.grupo_id AND x.materia_id=ch.materia_id").fetchone()["n"]
    cargas_configuradas = conn.execute("SELECT COUNT(*) n FROM cargas_horarias").fetchone()["n"]
    # Las restricciones de la base de datos impiden choques; esta auditoría también
    # detecta inconsistencias si la base fue modificada externamente.
    conflictos = 0
    for campo in ("profesor_id", "grupo_id", "salon_id"):
        conflictos += conn.execute(
            f"SELECT COUNT(*) n FROM (SELECT 1 FROM clases GROUP BY dia,franja_id,{campo} "
            "HAVING COUNT(*)>1)").fetchone()["n"]
    return {"profesores": profesores, "clases": clases, "horas": clases,
            "pendientes": pendientes, "pendientes_horas": pendientes_horas,
            "cargas_configuradas": cargas_configuradas,
            "salones_disponibles": max(0, salones-len(ocupados)),
            "conflictos": conflictos, "salones": salones, "franjas": franjas}


def generar_horario(conn, limite_nodos=150000):
    """Reemplaza el horario actual por uno que cumpla las cargas configuradas.

    Usa búsqueda con retroceso y ordena primero las clases con menos opciones.
    Si no encuentra solución, conserva intacto el horario anterior.
    """
    grupos = listar_catalogo(conn, "grupos")
    materias = listar_catalogo(conn, "materias")
    franjas = [f for f in listar_franjas(conn) if f["tipo"] == "clase"]
    salones = listar_catalogo(conn, "salones")
    if not grupos or not materias or not franjas or not salones:
        raise ErrorDeNegocio("Primero registra grupos, materias, franjas y salones")
    cargas = listar_cargas(conn)
    if not cargas or not any(x["horas"] for x in cargas):
        raise ErrorDeNegocio("Configura primero las horas semanales de las materias por grupo")
    profesores_materia = {}
    for r in conn.execute("SELECT profesor_id,materia_id FROM profesor_materias"):
        profesores_materia.setdefault(r["materia_id"], []).append(r["profesor_id"])
    instancias = []
    aulas_grupo = {r["grupo_id"]: r["salon_id"] for r in conn.execute(
        "SELECT grupo_id,salon_id FROM grupo_salon")}
    aulas_materia = {}
    for r in conn.execute("SELECT materia_id,salon_id FROM salon_materias"):
        aulas_materia.setdefault(r["materia_id"], []).append(r["salon_id"])
    for carga in cargas:
        if carga["horas"]:
            aulas_validas = aulas_materia.get(carga["materia_id"])
            if not aulas_validas:
                aula_grupo = aulas_grupo.get(carga["grupo_id"])
                if aula_grupo is None:
                    raise ErrorDeNegocio(
                        f"Asigna primero un aula principal al grupo {carga['grupo']}")
                aulas_validas = [aula_grupo]
            docentes = profesores_materia.get(carga["materia_id"], [])
            if not docentes:
                raise ErrorDeNegocio(
                    f"No hay profesor asignado a {carga['materia']} para {carga['grupo']}")
            for _ in range(carga["horas"]):
                instancias.append((carga["grupo_id"], carga["materia_id"], tuple(docentes), tuple(aulas_validas)))
    total_slots = 5 * len(franjas)
    por_grupo = {}
    for grupo, _, _, _ in instancias:
        por_grupo[grupo] = por_grupo.get(grupo, 0) + 1
    if any(n > total_slots for n in por_grupo.values()):
        raise ErrorDeNegocio("Un grupo tiene más horas asignadas que espacios disponibles en la semana")
    import random
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), len(instancias) + 300))

    def intentar(semilla):
        """Búsqueda con retroceso. Reparte las clases de una materia en días distintos."""
        random.seed(semilla)
        opciones = []
        for grupo, materia, docentes, aulas_validas in instancias:
            candidatos = [(d, f["id"], p, salon) for d in range(5) for f in franjas
                          for p in docentes for salon in aulas_validas]
            random.shuffle(candidatos)
            opciones.append((grupo, materia, candidatos))
        opciones.sort(key=lambda x: len(x[2]))
        usados_grupo, usados_prof, usados_salon = set(), set(), set()
        por_dia, carga_dia = {}, {}   # materia por día y clases totales del grupo por día
        solucion = [None] * len(opciones)
        nodos = 0

        def buscar(pos):
            nonlocal nodos
            if pos == len(opciones):
                return True
            if nodos >= limite_nodos:
                return False
            grupo, materia, candidatos = opciones[pos]
            preferidos = sorted(candidatos, key=lambda c: (
                por_dia.get((grupo, materia, c[0]), 0), carga_dia.get((grupo, c[0]), 0)))
            for d, franja, profesor, salon in preferidos:
                nodos += 1
                kg, kp, ks = (grupo, d, franja), (profesor, d, franja), (salon, d, franja)
                if kg in usados_grupo or kp in usados_prof or ks in usados_salon:
                    continue
                usados_grupo.add(kg); usados_prof.add(kp); usados_salon.add(ks)
                km, kd = (grupo, materia, d), (grupo, d)
                por_dia[km] = por_dia.get(km, 0) + 1
                carga_dia[kd] = carga_dia.get(kd, 0) + 1
                solucion[pos] = (materia, profesor, grupo, salon, d, franja)
                if buscar(pos + 1):
                    return True
                usados_grupo.remove(kg); usados_prof.remove(kp); usados_salon.remove(ks)
                por_dia[km] -= 1
                carga_dia[kd] -= 1
            return False

        return solucion if buscar(0) else None

    solucion = None
    for semilla in (42, 7, 123):
        solucion = intentar(semilla)
        if solucion:
            break
    if not solucion:
        raise ErrorDeNegocio(
            "No se encontró un horario completo sin cruces. Revisa las cargas, "
            "los profesores asignados y la cantidad de salones, e inténtalo de nuevo.")
    conn.commit()  # cierra cualquier operación pendiente (p. ej. la versión guardada antes)
    try:
        conn.execute("DELETE FROM clases")
        conn.executemany(
            "INSERT INTO clases (materia_id,profesor_id,grupo_id,salon_id,dia,franja_id) "
            "VALUES (?,?,?,?,?,?)", solucion)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(solucion)
