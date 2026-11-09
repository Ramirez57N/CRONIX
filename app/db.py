"""Conexión y esquema de la base de datos (SQLite)."""
import sqlite3
from pathlib import Path

from werkzeug.security import check_password_hash

RUTA_DB = Path(__file__).resolve().parent.parent / "horarios.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS profesores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS materias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS grupos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS salones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    tipo TEXT NOT NULL DEFAULT 'principal' CHECK (tipo IN ('principal', 'especial'))
);
CREATE TABLE IF NOT EXISTS franjas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orden INTEGER NOT NULL UNIQUE,
    inicio TEXT NOT NULL,   -- 'HH:MM'
    fin TEXT NOT NULL,      -- 'HH:MM'
    tipo TEXT NOT NULL DEFAULT 'clase' CHECK (tipo IN ('clase', 'descanso'))
);
CREATE TABLE IF NOT EXISTS clases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    materia_id INTEGER NOT NULL REFERENCES materias(id),
    profesor_id INTEGER NOT NULL REFERENCES profesores(id),
    grupo_id INTEGER NOT NULL REFERENCES grupos(id),
    salon_id INTEGER NOT NULL REFERENCES salones(id),
    dia INTEGER NOT NULL CHECK (dia BETWEEN 0 AND 4),  -- 0=lunes ... 4=viernes
    franja_id INTEGER NOT NULL REFERENCES franjas(id),
    -- Red de seguridad: la base de datos también impide los choques
    UNIQUE (profesor_id, dia, franja_id),
    UNIQUE (grupo_id, dia, franja_id),
    UNIQUE (salon_id, dia, franja_id)
);
CREATE TABLE IF NOT EXISTS cargas_horarias (
    grupo_id INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
    materia_id INTEGER NOT NULL REFERENCES materias(id) ON DELETE CASCADE,
    horas INTEGER NOT NULL CHECK (horas BETWEEN 0 AND 35),
    PRIMARY KEY (grupo_id, materia_id)
);
CREATE TABLE IF NOT EXISTS grupo_salon (
    grupo_id INTEGER PRIMARY KEY REFERENCES grupos(id) ON DELETE CASCADE,
    salon_id INTEGER NOT NULL REFERENCES salones(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS salon_materias (
    salon_id INTEGER NOT NULL REFERENCES salones(id) ON DELETE CASCADE,
    materia_id INTEGER NOT NULL REFERENCES materias(id) ON DELETE CASCADE,
    PRIMARY KEY (salon_id, materia_id)
);
CREATE TABLE IF NOT EXISTS profesor_materias (
    profesor_id INTEGER NOT NULL REFERENCES profesores(id),
    materia_id INTEGER NOT NULL REFERENCES materias(id),
    PRIMARY KEY (profesor_id, materia_id)
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    usuario TEXT NOT NULL,
    accion TEXT NOT NULL,
    detalle TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ausencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profesor_id INTEGER NOT NULL REFERENCES profesores(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    motivo TEXT NOT NULL,
    observaciones TEXT DEFAULT '',
    UNIQUE(profesor_id, fecha)
);
CREATE TABLE IF NOT EXISTS reemplazos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ausencia_id INTEGER NOT NULL REFERENCES ausencias(id) ON DELETE CASCADE,
    clase_id INTEGER NOT NULL REFERENCES clases(id) ON DELETE CASCADE,
    profesor_reemplazo_id INTEGER NOT NULL REFERENCES profesores(id),
    UNIQUE(clase_id, ausencia_id)
);
CREATE TABLE IF NOT EXISTS versiones_horario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    usuario TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    datos TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT NOT NULL UNIQUE,
    clave_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('rector', 'profesor')),
    profesor_id INTEGER UNIQUE REFERENCES profesores(id)
);
CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def conectar(ruta=RUTA_DB):
    conn = sqlite3.connect(ruta, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def iniciar_db(conn):
    conn.executescript(ESQUEMA)
    # Migración: las franjas ahora pueden ser de clase o de descanso.
    if "tipo" not in {r[1] for r in conn.execute("PRAGMA table_info(franjas)")}:
        conn.execute("ALTER TABLE franjas ADD COLUMN tipo TEXT NOT NULL DEFAULT 'clase' "
                     "CHECK (tipo IN ('clase', 'descanso'))")
    # Migración compatible con bases de datos creadas antes de clasificar las aulas.
    columnas = {r[1] for r in conn.execute("PRAGMA table_info(salones)")}
    if "tipo" not in columnas:
        conn.execute("ALTER TABLE salones ADD COLUMN tipo TEXT NOT NULL DEFAULT 'principal'")
    # Las aulas que ya tenían materias asociadas en versiones anteriores se
    # reconocen automáticamente como especializadas al actualizar la base.
    conn.execute("UPDATE salones SET tipo='especial' WHERE id IN "
                 "(SELECT DISTINCT salon_id FROM salon_materias)")
    # Migración: marca de "clave inicial" para avisar al rector qué profesores aún no la cambian.
    if "clave_inicial" not in {r[1] for r in conn.execute("PRAGMA table_info(usuarios)")}:
        conn.execute("ALTER TABLE usuarios ADD COLUMN clave_inicial INTEGER NOT NULL DEFAULT 0")
        for u in conn.execute("SELECT id, clave_hash FROM usuarios WHERE rol = 'profesor'").fetchall():
            if check_password_hash(u["clave_hash"], "123456"):
                conn.execute("UPDATE usuarios SET clave_inicial = 1 WHERE id = ?", (u["id"],))
    conn.commit()
