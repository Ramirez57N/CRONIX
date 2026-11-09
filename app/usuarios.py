"""Cuentas de acceso: rector (administrador) y profesores."""
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from .servicio import Conflicto, ErrorDeNegocio


def hay_rector(conn):
    return conn.execute("SELECT 1 FROM usuarios WHERE rol = 'rector'").fetchone() is not None


def _usuario(usuario):
    usuario = (usuario or "").strip().lower()
    if len(usuario) < 3:
        raise ErrorDeNegocio("El usuario debe tener al menos 3 caracteres")
    return usuario


def _clave(clave):
    if len(clave or "") < 6:
        raise ErrorDeNegocio("La contraseña debe tener al menos 6 caracteres")
    return generate_password_hash(clave)


def crear_usuario(conn, usuario, clave, rol, profesor_id=None):
    usuario, hash_ = _usuario(usuario), _clave(clave)
    try:
        conn.execute(
            "INSERT INTO usuarios (usuario, clave_hash, rol, profesor_id, clave_inicial) "
            "VALUES (?, ?, ?, ?, ?)",
            (usuario, hash_, rol, profesor_id, 1 if rol == "profesor" else 0),
        )
    except sqlite3.IntegrityError:
        raise Conflicto("Ese usuario ya existe, o el profesor ya tiene acceso")
    conn.commit()


def establecer_acceso(conn, profesor_id, usuario, clave):
    """Crea el acceso de un profesor o, si ya tiene, le cambia la contraseña."""
    if conn.execute("SELECT 1 FROM usuarios WHERE profesor_id = ?", (profesor_id,)).fetchone():
        conn.execute("UPDATE usuarios SET clave_hash = ?, clave_inicial = 1 WHERE profesor_id = ?",
                     (_clave(clave), profesor_id))
        conn.commit()
    else:
        crear_usuario(conn, usuario, clave, "profesor", profesor_id)


def autenticar(conn, usuario, clave):
    fila = conn.execute("SELECT * FROM usuarios WHERE usuario = ?",
                        ((usuario or "").strip().lower(),)).fetchone()
    if fila and check_password_hash(fila["clave_hash"], clave or ""):
        return dict(fila)
    return None


def cambiar_clave(conn, usuario, actual, nueva):
    """Cambia la contraseña de quien la conoce (rector o profesor)."""
    if autenticar(conn, usuario, actual) is None:
        raise ErrorDeNegocio("La contraseña actual no es correcta")
    conn.execute("UPDATE usuarios SET clave_hash = ?, clave_inicial = 0 WHERE usuario = ?",
                 (_clave(nueva), usuario.strip().lower()))
    conn.commit()


def con_clave_inicial(conn):
    """Profesores que todavía no han cambiado la clave que les dio el rector."""
    return conn.execute(
        "SELECT u.usuario, p.nombre FROM usuarios u JOIN profesores p ON p.id = u.profesor_id "
        "WHERE u.rol = 'profesor' AND u.clave_inicial = 1 ORDER BY p.nombre").fetchall()


def listar_rectores(conn):
    return conn.execute("SELECT id, usuario FROM usuarios WHERE rol = 'rector' ORDER BY usuario").fetchall()


def restablecer_clave_rector(conn, usuario_id, clave):
    """Pone una contraseña nueva a un rector y devuelve su nombre de usuario."""
    hash_ = _clave(clave)
    fila = conn.execute("SELECT usuario FROM usuarios WHERE id = ? AND rol = 'rector'",
                        (usuario_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio("No se encontró ese rector")
    conn.execute("UPDATE usuarios SET clave_hash = ? WHERE id = ?", (hash_, usuario_id))
    conn.commit()
    return fila["usuario"]
