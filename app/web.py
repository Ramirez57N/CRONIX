"""Interfaz web con login (Flask). Roles: rector (administra) y profesor (consulta)."""
import base64
import secrets
import json
import shutil
import sqlite3
from datetime import date, timedelta
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)

from . import servicio, usuarios
from .db import RUTA_DB, conectar, iniciar_db
from werkzeug.security import generate_password_hash, check_password_hash
import re

CAMPOS = ["materia_id", "profesor_id", "grupo_id", "salon_id", "dia", "franja_id"]
TABLAS = ("profesores", "materias", "grupos", "salones", "franjas")
COLOR_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _tipo_imagen(datos):
    """Reconoce PNG, JPG, WEBP o GIF por su contenido (no por el nombre del archivo)."""
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    if datos[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def crear_app(ruta_db=RUTA_DB):
    """Crea la aplicación. Cada institución nueva utiliza su propia base SQLite."""
    datos_dir = Path(ruta_db).resolve().parent / "instituciones"
    datos_dir.mkdir(parents=True, exist_ok=True)
    central_db = Path(ruta_db).resolve().parent / "plataforma.db"

    class ConexionCentral:
        def __enter__(self):
            self.conn = sqlite3.connect(central_db, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            return self.conn
        def __exit__(self, tipo, valor, traza):
            self.conn.close()

    def central():
        return ConexionCentral()

    with central() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS propietario (
            id INTEGER PRIMARY KEY CHECK(id=1), usuario TEXT UNIQUE NOT NULL,
            clave_hash TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS instituciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            codigo TEXT UNIQUE NOT NULL, db_archivo TEXT NOT NULL,
            creada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            activa INTEGER NOT NULL DEFAULT 1)""")
        # Migración automática para instalaciones creadas con una versión anterior.
        columnas = {fila[1] for fila in c.execute("PRAGMA table_info(instituciones)")}
        if "activa" not in columnas:
            c.execute("ALTER TABLE instituciones ADD COLUMN activa INTEGER NOT NULL DEFAULT 1")
        if "ultimo_ingreso" not in columnas:
            c.execute("ALTER TABLE instituciones ADD COLUMN ultimo_ingreso TEXT")
        c.execute("""CREATE TABLE IF NOT EXISTS auditoria_soporte (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            propietario TEXT NOT NULL, institucion TEXT NOT NULL, accion TEXT NOT NULL)""")
        # La instalación original se conserva como primera institución, sin copiar ni borrar sus datos.
        c.execute("INSERT OR IGNORE INTO instituciones(nombre,codigo,db_archivo) VALUES(?,?,?)",
                  ("Institución actual", "institucion-actual", str(Path(ruta_db).resolve())))

    app = Flask(__name__)
    llave = Path(__file__).resolve().parent.parent / "secret.key"
    if not llave.exists():
        llave.write_text(secrets.token_hex(32))
    app.secret_key = llave.read_text()
    app.config.update(SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=2 * 1024 * 1024)
    app.config["RUTA_DB"] = ruta_db
    inicial = conectar(ruta_db)
    iniciar_db(inicial)

    migradas = set()

    def abrir_base(ruta):
        """Abre la base de un colegio y la actualiza (una vez por arranque) si viene de una versión anterior."""
        conn = conectar(ruta)
        if str(ruta) not in migradas:
            iniciar_db(conn)
            migradas.add(str(ruta))
        return conn
    inicial.close()

    def db():
        if "conn" not in g:
            ruta = app.config["RUTA_DB"]
            codigo = session.get("institucion_codigo")
            if codigo:
                with central() as c:
                    reg = c.execute("SELECT db_archivo, activa FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
                if reg and reg["activa"]:
                    ruta = reg["db_archivo"]
                else:
                    session.clear()
                    abort(403)
            g.conn = abrir_base(ruta)
        return g.conn

    @app.teardown_appcontext
    def cerrar(_):
        conn = g.pop("conn", None)
        if conn:
            conn.close()

    @app.context_processor
    def datos_del_colegio():
        """Nombre, color y logo del colegio para todas las plantillas."""
        datos = {"nombre_colegio": None, "color_colegio": None, "logo_colegio": None}
        codigo = session.get("institucion_codigo")
        if not codigo:
            return datos
        try:
            with central() as c:
                fila = c.execute("SELECT nombre FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
            if fila:
                datos["nombre_colegio"] = fila["nombre"]
            cfg = {r["clave"]: r["valor"] for r in db().execute("SELECT clave, valor FROM configuracion")}
            if COLOR_HEX.fullmatch(cfg.get("color", "")):
                datos["color_colegio"] = cfg["color"]
            if cfg.get("logo", "").startswith("data:image/"):
                datos["logo_colegio"] = cfg["logo"]
        except Exception:
            app.logger.exception("No se pudo leer la personalización del colegio")
        return datos

    @app.after_request
    def auditar_acciones_soporte(respuesta):
        # La actividad de soporte se guarda SOLO en la base central del propietario,
        # nunca en el historial del colegio que puede consultar el rector.
        if session.get("modo_soporte") and request.method == "POST":
            try:
                codigo = session.get("institucion_codigo")
                with central() as c:
                    inst = c.execute("SELECT nombre FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
                    if inst:
                        c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                                  (session.get("propietario", "propietario"), inst["nombre"],
                                   f"Acción de soporte: {request.endpoint or request.path}"))
            except Exception:
                app.logger.exception("No se pudo registrar una acción del modo soporte")
        return respuesta

    def requiere(rol=None):
        def deco(f):
            @wraps(f)
            def envuelta(*a, **k):
                if "usuario" not in session:
                    if session.get("institucion_codigo"):
                        return redirect(url_for("colegio_login"))
                    return redirect(url_for("login"))
                if not usuarios.hay_rector(db()):
                    return redirect(url_for("configurar"))
                if rol and session["rol"] != rol:
                    abort(403)
                if session.get("rol") == "profesor":
                    g.profesor_id = session.get("profesor_id")
                return f(*a, **k)
            return envuelta
        return deco

    @app.errorhandler(servicio.ErrorDeNegocio)
    def error_negocio(e):
        flash(str(e), "error")
        return redirect(request.referrer or url_for("inicio"))

    # ----- Consola del propietario de la plataforma -----
    @app.route("/propietario/configurar", methods=["GET", "POST"])
    def propietario_configurar():
        with central() as c:
            existe = c.execute("SELECT 1 FROM propietario WHERE id=1").fetchone()
        if existe:
            return redirect(url_for("propietario_login"))
        if request.method == "POST":
            u = (request.form.get("usuario") or "").strip().lower()
            clave = request.form.get("clave") or ""
            if len(u) < 3 or len(clave) < 10:
                flash("Usa un usuario de al menos 3 caracteres y una contraseña de 10 o más.", "error")
            else:
                with central() as c:
                    c.execute("INSERT INTO propietario(id,usuario,clave_hash) VALUES(1,?,?)",
                              (u, generate_password_hash(clave)))
                flash("Cuenta principal creada. Inicia sesión.")
                return redirect(url_for("propietario_login"))
        return render_template("propietario_acceso.html", titulo="Crear cuenta del propietario",
                               boton="Crear propietario")

    @app.route("/propietario/login")
    def propietario_login():
        return redirect(url_for("ingreso"))

    def propietario_requerido():
        if not session.get("propietario"):
            return redirect(url_for("propietario_login"))
        return None

    @app.route("/propietario", methods=["GET", "POST"])
    def propietario():
        redir = propietario_requerido()
        if redir: return redir
        if request.method == "POST":
            nombre = (request.form.get("nombre") or "").strip()
            codigo = (request.form.get("codigo") or "").strip().lower()
            usuario = (request.form.get("usuario") or "").strip().lower()
            clave = request.form.get("clave") or ""
            if not nombre or not re.fullmatch(r"[a-z0-9-]{3,30}", codigo):
                flash("Nombre obligatorio y código de 3 a 30 caracteres (letras, números o guiones).", "error")
            elif len(usuario) < 3 or len(clave) < 6:
                flash("El usuario debe tener 3 caracteres y la contraseña al menos 6.", "error")
            else:
                archivo = (datos_dir / f"{codigo}.db").resolve()
                try:
                    with central() as c:
                        c.execute("INSERT INTO instituciones(nombre,codigo,db_archivo) VALUES(?,?,?)",
                                  (nombre, codigo, str(archivo)))
                    nuevo = conectar(archivo)
                    iniciar_db(nuevo)
                    usuarios.crear_usuario(nuevo, usuario, clave, "rector")
                    nuevo.close()
                    flash(f"Institución {nombre} creada. Código de acceso: {codigo}")
                except sqlite3.IntegrityError:
                    flash("Ese código de institución ya está registrado.", "error")
                except Exception:
                    with central() as c: c.execute("DELETE FROM instituciones WHERE codigo=?", (codigo,))
                    raise
                return redirect(url_for("propietario"))
        with central() as c:
            instituciones = [dict(r) for r in c.execute("SELECT * FROM instituciones ORDER BY nombre")]
        for i in instituciones:
            i["profesores"] = i["grupos"] = None
            if not Path(i["db_archivo"]).is_file():
                continue
            try:
                conn = conectar(i["db_archivo"])
                try:
                    i["profesores"] = conn.execute("SELECT COUNT(*) FROM profesores").fetchone()[0]
                    i["grupos"] = conn.execute("SELECT COUNT(*) FROM grupos").fetchone()[0]
                finally:
                    conn.close()
            except sqlite3.Error:
                pass  # si el archivo no se puede leer, la consola muestra "—"
        return render_template("propietario.html", instituciones=instituciones)

    @app.route("/propietario/estado/<codigo>", methods=["POST"])
    def propietario_estado(codigo):
        redir = propietario_requerido()
        if redir: return redir
        estado = request.form.get("estado")
        if estado not in ("0", "1"):
            abort(400)
        with central() as c:
            inst = c.execute("SELECT nombre FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
            if not inst: abort(404)
            c.execute("UPDATE instituciones SET activa=? WHERE codigo=?", (int(estado), codigo))
            c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                      (session["propietario"], inst["nombre"],
                       "Institución activada" if estado == "1" else "Institución desactivada"))
        # Si se desactiva el colegio que está abierto en modo soporte, se cierra esa sesión.
        if estado == "0" and session.get("institucion_codigo") == codigo:
            session.clear()
        flash("Institución actualizada correctamente.")
        return redirect(url_for("propietario_login") if estado == "0" and not session.get("propietario") else url_for("propietario"))

    @app.route("/propietario/eliminar/<codigo>", methods=["POST"])
    def propietario_eliminar(codigo):
        redir = propietario_requerido()
        if redir: return redir
        if session.get("modo_soporte"):
            abort(403)
        if (request.form.get("confirmacion") or "").strip().lower() != codigo.lower():
            flash("El código de confirmación no coincide. No se eliminó la institución.", "error")
            return redirect(url_for("propietario"))
        with central() as c:
            inst = c.execute("SELECT * FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
            if not inst: abort(404)
            c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                      (session["propietario"], inst["nombre"], "Institución eliminada del registro central"))
            c.execute("DELETE FROM instituciones WHERE codigo=?", (codigo,))
        # Solo se borra un archivo .db ubicado dentro de la carpeta administrada por esta app.
        archivo = Path(inst["db_archivo"]).resolve()
        raiz = datos_dir.resolve()
        try:
            archivo.relative_to(raiz)
            es_archivo_seguro = archivo.suffix == ".db" and archivo.is_file()
        except ValueError:
            es_archivo_seguro = False
        if es_archivo_seguro:
            archivo.unlink()
        flash("Institución eliminada del panel central. Haz siempre una copia de seguridad antes de eliminar.")
        return redirect(url_for("propietario"))

    @app.route("/propietario/entrar/<codigo>", methods=["POST"])
    def propietario_entrar(codigo):
        redir = propietario_requerido()
        if redir: return redir
        with central() as c:
            inst = c.execute("SELECT * FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
        if not inst: abort(404)
        if not inst["activa"]:
            flash("Esta institución está desactivada. Reactívala desde el panel central para ingresar.", "error")
            return redirect(url_for("propietario"))
        session["institucion_codigo"] = codigo
        session["usuario"] = session["propietario"]
        session["rol"] = "rector"
        session["profesor_id"] = None
        session["modo_soporte"] = True
        with central() as c:
            c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                      (session["propietario"], inst["nombre"], "Ingreso en modo soporte"))
        return redirect(url_for("inicio"))

    @app.route("/propietario/salir-soporte")
    def propietario_salir_soporte():
        if not session.get("modo_soporte"): return redirect(url_for("propietario"))
        codigo = session.get("institucion_codigo")
        with central() as c:
            inst = c.execute("SELECT nombre FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
            if inst:
                c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                          (session.get("propietario", "propietario"), inst["nombre"], "Salida del modo soporte"))
        session.pop("institucion_codigo", None); session.pop("usuario", None)
        session.pop("rol", None); session.pop("profesor_id", None); session.pop("modo_soporte", None)
        return redirect(url_for("propietario"))

    def abrir_institucion(codigo):
        with central() as c:
            inst = c.execute("SELECT * FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
        if not inst:
            abort(404)
        return inst, abrir_base(inst["db_archivo"])

    def anotar_soporte(institucion, accion):
        with central() as c:
            c.execute("INSERT INTO auditoria_soporte(propietario,institucion,accion) VALUES(?,?,?)",
                      (session["propietario"], institucion, accion))

    @app.route("/propietario/<codigo>/rectores", methods=["GET", "POST"])
    def propietario_rectores(codigo):
        redir = propietario_requerido()
        if redir: return redir
        inst, conn = abrir_institucion(codigo)
        try:
            if request.method == "POST":
                nuevo = (request.form.get("usuario") or "").strip().lower()
                usuarios.crear_usuario(conn, nuevo, request.form.get("clave"), "rector")
                anotar_soporte(inst["nombre"], f"Rector creado: {nuevo}")
                flash(f"Rector {nuevo} creado.")
                return redirect(url_for("propietario_rectores", codigo=codigo))
            rectores = usuarios.listar_rectores(conn)
        finally:
            conn.close()
        return render_template("propietario_rectores.html", inst=inst, rectores=rectores)

    @app.route("/propietario/<codigo>/rectores/<int:uid>/clave", methods=["POST"])
    def propietario_rector_clave(codigo, uid):
        redir = propietario_requerido()
        if redir: return redir
        inst, conn = abrir_institucion(codigo)
        try:
            nombre = usuarios.restablecer_clave_rector(conn, uid, request.form.get("clave"))
        finally:
            conn.close()
        anotar_soporte(inst["nombre"], f"Contraseña restablecida al rector: {nombre}")
        flash(f"Contraseña de {nombre} actualizada.")
        return redirect(url_for("propietario_rectores", codigo=codigo))

    @app.route("/propietario/auditoria")
    def propietario_auditoria():
        redir = propietario_requerido()
        if redir: return redir
        with central() as c:
            filas = c.execute("SELECT fecha, propietario, institucion, accion FROM auditoria_soporte "
                              "ORDER BY id DESC LIMIT 300").fetchall()
        return render_template("propietario_auditoria.html", filas=filas)

    @app.route("/propietario/respaldo/<codigo>", methods=["POST"])
    def propietario_respaldo(codigo):
        redir = propietario_requerido()
        if redir: return redir
        inst, origen = abrir_institucion(codigo)
        carpeta = Path(ruta_db).resolve().parent / "respaldos_plataforma"
        carpeta.mkdir(exist_ok=True)
        destino = carpeta / f"{codigo}-{datetime.now():%Y%m%d-%H%M%S}.db"
        try:
            copia = sqlite3.connect(destino)
            try:
                origen.backup(copia)
            finally:
                copia.close()
        finally:
            origen.close()
        anotar_soporte(inst["nombre"], f"Copia de seguridad creada: {destino.name}")
        flash(f"Copia creada en respaldos_plataforma/{destino.name}")
        return redirect(url_for("propietario"))

    @app.route("/propietario/salir")
    def propietario_salir():
        session.clear()
        return redirect(url_for("propietario_login"))

    @app.route("/colegio")
    def colegio_login():
        return redirect(url_for("ingreso"))

    @app.route("/ingresar", methods=["GET", "POST"])
    def ingreso():
        """Pantalla única de ingreso: propietario, rector o profesor."""
        tipo = ""
        if request.method == "POST":
            tipo = request.form.get("tipo", "")
            usuario = (request.form.get("usuario") or "").strip().lower()
            clave = request.form.get("clave") or ""
            codigo = (request.form.get("codigo") or "").strip().lower()
            if tipo == "propietario":
                with central() as c:
                    hay = c.execute("SELECT 1 FROM propietario WHERE id=1").fetchone()
                    p = c.execute("SELECT * FROM propietario WHERE usuario=?", (usuario,)).fetchone()
                if not hay:
                    return redirect(url_for("propietario_configurar"))
                if p and check_password_hash(p["clave_hash"], clave):
                    session.clear()
                    session["propietario"] = p["usuario"]
                    return redirect(url_for("propietario"))
            elif tipo in ("rector", "profesor"):
                with central() as c:
                    inst = c.execute("SELECT * FROM instituciones WHERE codigo=?", (codigo,)).fetchone()
                if inst and inst["activa"]:
                    conn = conectar(inst["db_archivo"])
                    try:
                        u = usuarios.autenticar(conn, usuario, clave)
                    finally:
                        conn.close()
                    # El tipo elegido debe coincidir con el rol real de la cuenta.
                    if u and u["rol"] == tipo:
                        with central() as c:
                            c.execute("UPDATE instituciones SET ultimo_ingreso=CURRENT_TIMESTAMP WHERE codigo=?", (codigo,))
                        session.clear()
                        session.update(institucion_codigo=codigo, usuario=u["usuario"],
                                       rol=u["rol"], profesor_id=u["profesor_id"])
                        return redirect(url_for("inicio"))
            flash("Datos incorrectos. Revisa el tipo de usuario, el código del colegio, el usuario y la contraseña.", "error")
        with central() as c:
            hay_propietario = c.execute("SELECT 1 FROM propietario WHERE id=1").fetchone() is not None
        return render_template("ingreso.html", tipo=tipo, hay_propietario=hay_propietario)

    # ----- Acceso -----
    @app.route("/configurar", methods=["GET", "POST"])
    def configurar():
        if usuarios.hay_rector(db()):
            return redirect(url_for("login"))
        if request.method == "POST":
            usuarios.crear_usuario(db(), request.form["usuario"], request.form["clave"], "rector")
            flash("Cuenta del rector creada. Ya puedes ingresar.")
            return redirect(url_for("login"))
        return render_template("acceso.html", titulo="Crear cuenta del rector", boton="Crear cuenta")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return redirect(url_for("ingreso"))
        if not usuarios.hay_rector(db()):
            return redirect(url_for("configurar"))
        if request.method == "POST":
            u = usuarios.autenticar(db(), request.form["usuario"], request.form["clave"])
            if u:
                session.clear()
                session.update(usuario=u["usuario"], rol=u["rol"], profesor_id=u["profesor_id"])
                return redirect(url_for("inicio"))
            flash("Usuario o contraseña incorrectos", "error")
        return render_template("acceso.html", titulo="Ingresar", boton="Entrar")

    @app.route("/horario/imprimir")
    @requiere()
    def imprimir_horario():
        conn = db()
        ver = (f"profesor:{session['profesor_id']}" if session["rol"] == "profesor"
               else request.args.get("ver", ""))
        tipo, _, i = ver.partition(":")
        tablas = {"grupo": "grupos", "profesor": "profesores", "salon": "salones"}
        if tipo not in tablas or not i.isdigit():
            abort(404)
        fila = conn.execute(f"SELECT nombre FROM {tablas[tipo]} WHERE id = ?", (int(i),)).fetchone()
        if fila is None:
            abort(404)
        clases = servicio.listar_clases(conn, **{f"{tipo}_id": int(i)})
        return render_template("imprimir_horario.html", tipo=tipo, nombre=fila["nombre"],
                               celdas={(c["dia"], c["franja_id"]): c for c in clases},
                               franjas=servicio.listar_franjas(conn), dias=servicio.DIAS,
                               hoy=date.today().strftime("%d/%m/%Y"))

    @app.route("/mi-horario/imprimir")
    @requiere("profesor")
    def imprimir_mi_horario():
        return redirect(url_for("imprimir_horario"))

    @app.route("/salir")
    def salir():
        if session.get("modo_soporte"):
            return redirect(url_for("propietario_salir_soporte"))
        era_colegio = bool(session.get("institucion_codigo"))
        session.clear()
        return redirect(url_for("colegio_login") if era_colegio else url_for("login"))

    # ----- Consulta (rector y profesores) -----
    @app.route("/")
    @requiere()
    def inicio():
        conn = db()
        e = servicio.estado_ahora(conn)
        hoy = date.today()
        if session["rol"] == "rector":
            def contar(sql):
                return conn.execute(sql).fetchone()[0]
            de_hoy = [a for a in servicio.ausencias_con_clases(conn, desde=hoy.isoformat())
                      if a["fecha"] == hoy.isoformat()]
            stats = servicio.estadisticas(conn)
            franjas = contar("SELECT COUNT(*) FROM franjas WHERE tipo = 'clase'")
            profes, con_materias = (contar("SELECT COUNT(*) FROM profesores"),
                                    contar("SELECT COUNT(DISTINCT profesor_id) FROM profesor_materias"))
            grupos, con_aula = contar("SELECT COUNT(*) FROM grupos"), contar("SELECT COUNT(*) FROM grupo_salon")
            pasos = [
                {"ok": franjas > 0, "texto": f"Franjas de clase creadas ({franjas})",
                 "url": url_for("admin", tabla="franjas")},
                {"ok": profes > 0 and con_materias == profes,
                 "texto": f"Profesores con materias asignadas ({con_materias} de {profes})",
                 "url": url_for("admin", tabla="profesores")},
                {"ok": grupos > 0 and con_aula == grupos,
                 "texto": f"Grupos con aula principal ({con_aula} de {grupos})", "url": url_for("aulas")},
                {"ok": stats["cargas_configuradas"] > 0,
                 "texto": f"Intensidad horaria configurada ({stats['cargas_configuradas']} materias de grupo)",
                 "url": url_for("cargas")},
            ]
            return render_template("inicio.html", e=e, stats=stats, pasos=pasos,
                                   claves_iniciales=usuarios.con_clave_inicial(conn),
                                   ausentes_hoy=len(de_hoy),
                                   sin_cubrir_hoy=sum(a["sin_cubrir"] for a in de_hoy))
        pid = session["profesor_id"]
        mis_clases = servicio.listar_clases(conn, profesor_id=pid)
        fila = conn.execute("SELECT nombre FROM profesores WHERE id = ?", (pid,)).fetchone()
        reemplazos = conn.execute(
            "SELECT a.fecha, m.nombre materia, g.nombre grupo, s.nombre salon, f.inicio, f.fin "
            "FROM reemplazos r JOIN ausencias a ON a.id = r.ausencia_id JOIN clases c ON c.id = r.clase_id "
            "JOIN materias m ON m.id = c.materia_id JOIN grupos g ON g.id = c.grupo_id "
            "JOIN salones s ON s.id = c.salon_id JOIN franjas f ON f.id = c.franja_id "
            "WHERE r.profesor_reemplazo_id = ? AND a.fecha >= ? ORDER BY a.fecha, f.inicio",
            (pid, hoy.isoformat())).fetchall()
        return render_template("inicio.html", e=e, stats=None, mis_clases=mis_clases,
                               clases_hoy=[c for c in mis_clases if c["dia"] == hoy.weekday()],
                               nombre=fila["nombre"] if fila else session["usuario"],
                               dias=servicio.DIAS, reemplazos=reemplazos)

    @app.route("/admin/cargas", methods=["GET", "POST"])
    @requiere("rector")
    def cargas():
        conn = db()
        grupos = servicio.listar_catalogo(conn, "grupos")
        materias = servicio.listar_catalogo(conn, "materias")
        grupo_id = int(request.form.get("grupo_id") or request.args.get("grupo_id") or
                       (grupos[0]["id"] if grupos else 0))
        if request.method == "POST":
            for materia in materias:
                valor = request.form.get(f"horas_{materia['id']}", "").strip()
                horas = int(valor) if valor else 0
                if horas:
                    servicio.guardar_carga(conn, grupo_id, materia["id"], horas)
                else:
                    servicio.eliminar_carga(conn, grupo_id, materia["id"])
            flash("Cargas académicas del grupo actualizadas")
            return redirect(url_for("cargas", grupo_id=grupo_id))
        if request.method == "GET" and request.args.get("grupo_id"):
            grupo_id = int(request.args["grupo_id"])
        cargas = {c["materia_id"]: c["horas"] for c in servicio.listar_cargas(conn)
                  if c["grupo_id"] == grupo_id}
        espacios = 5 * conn.execute("SELECT COUNT(*) FROM franjas WHERE tipo = 'clase'").fetchone()[0]
        resumen = {}
        for c in servicio.listar_cargas(conn):
            r = resumen.setdefault(c["grupo_id"], {"id": c["grupo_id"], "grupo": c["grupo"],
                                                   "materias": 0, "horas": 0})
            r["materias"] += 1
            r["horas"] += c["horas"]
        resumen = sorted(resumen.values(), key=lambda r: servicio._clave_natural(r["grupo"]))
        return render_template("cargas.html", grupos=grupos, materias=materias,
                               grupo_id=grupo_id, cargas_grupo=cargas,
                               resumen=resumen, espacios=espacios)

    @app.route("/admin/cargas/eliminar", methods=["POST"])
    @requiere("rector")
    def eliminar_carga():
        grupo_id = int(request.form["grupo_id"])
        materia_id = int(request.form["materia_id"])
        servicio.eliminar_carga(db(), grupo_id, materia_id)
        flash("Materia eliminada de la intensidad horaria del grupo")
        return redirect(url_for("cargas", grupo_id=grupo_id))

    @app.route("/admin/generar", methods=["POST"])
    @requiere("rector")
    def generar():
        conn=db()
        antes=[dict(r) for r in conn.execute("SELECT materia_id,profesor_id,grupo_id,salon_id,dia,franja_id FROM clases ORDER BY id")]
        if antes:
            conn.execute("INSERT INTO versiones_horario(usuario,descripcion,datos) VALUES(?,?,?)",(session["usuario"],"Antes de generar nuevo horario",json.dumps(antes)))
        reemplazos_previos = conn.execute("SELECT COUNT(*) FROM reemplazos").fetchone()[0]
        total = servicio.generar_horario(conn)
        conn.execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",(session["usuario"],"Generación automática",f"{total} clases generadas")); conn.commit()
        flash(f"Horario generado correctamente: {total} clases distribuidas.")
        if reemplazos_previos:
            flash(f"Se eliminaron {reemplazos_previos} reemplazo(s) porque las clases cambiaron. "
                  "Vuelve a asignarlos en Ausencias.", "error")
        return redirect(url_for("inicio"))

    @app.route("/horario")
    @requiere()
    def horario():
        conn = db()
        if session["rol"] == "profesor":
            ver = f"profesor:{session['profesor_id']}"
        else:
            ver = request.args.get("ver", "")
        tipo, _, i = ver.partition(":")
        celdas = None
        if tipo in ("grupo", "profesor", "salon") and i.isdigit():
            clases = servicio.listar_clases(conn, **{f"{tipo}_id": int(i)})
            celdas = {(c["dia"], c["franja_id"]): c for c in clases}
        opciones = [("Grupos", "grupo", servicio.listar_catalogo(conn, "grupos")),
                    ("Profesores", "profesor", servicio.listar_catalogo(conn, "profesores")),
                    ("Salones", "salon", servicio.listar_catalogo(conn, "salones"))]
        nombre_sel = None
        if celdas is not None:
            tabla_sel = {"grupo": "grupos", "profesor": "profesores", "salon": "salones"}[tipo]
            fila = conn.execute(f"SELECT nombre FROM {tabla_sel} WHERE id = ?", (int(i),)).fetchone()
            nombre_sel = fila["nombre"] if fila else None
        return render_template("horario.html", ver=ver, tipo=tipo, i=i, celdas=celdas,
                               opciones=opciones, franjas=servicio.listar_franjas(conn),
                               dias=servicio.DIAS, nombre_sel=nombre_sel)

    @app.route("/libres")
    @requiere()
    def libres():
        conn = db()
        franjas = servicio.listar_franjas(conn)
        profesores = servicio.listar_catalogo(conn, "profesores")
        profesor_id = request.args.get("profesor_id", type=int)
        clases = servicio.listar_clases(conn, profesor_id=profesor_id) if profesor_id else []
        celdas = {}
        if profesor_id:
            celdas = {(c["dia"], c["franja_id"]): c for c in clases}
        else:
            for d in range(5):
                for f in franjas:
                    celdas[(d, f["id"])] = servicio.profesores_libres(conn, d, f["id"])
        return render_template("libres.html", franjas=franjas, dias=servicio.DIAS,
                               profesores=profesores, profesor_id=profesor_id, celdas=celdas)

    @app.route("/admin/aulas", methods=["GET", "POST"])
    @requiere("rector")
    def aulas():
        conn = db()
        if request.method == "POST":
            accion = request.form.get("accion")
            if accion == "grupo":
                servicio.asignar_salon_grupo(conn, int(request.form["grupo_id"]), int(request.form["salon_id"]))
                flash("Aula principal del grupo guardada")
            elif accion == "materias":
                servicio.asignar_materias_salon(conn, int(request.form["salon_id"]),
                                                [int(x) for x in request.form.getlist("materias")])
                flash("Materias del aula especializada guardadas")
            elif accion == "tipo":
                servicio.cambiar_tipo_aula(conn, int(request.form["salon_id"]), request.form.get("tipo"))
                flash("Tipo de aula actualizado")
            elif accion == "crear":
                servicio.crear_aula(conn, request.form.get("nombre"), request.form.get("tipo"))
                flash("Aula creada y clasificada correctamente")
            return redirect(url_for("aulas"))
        grupos, especiales = servicio.asignaciones_aulas(conn)
        return render_template("aulas.html", grupos=grupos,
                               salones=servicio.listar_aulas(conn),
                               aulas_principales=servicio.listar_aulas(conn, "principal"),
                               aulas_especiales=servicio.listar_aulas(conn, "especial"),
                               materias=servicio.listar_catalogo(conn, "materias"), especiales=especiales,
                               grupo_por_aula={g["salon_id"]: g["grupo"] for g in grupos if g["salon_id"]})

    @app.route("/admin/conflictos")
    @requiere("rector")
    def conflictos():
        conn = db()
        clases = servicio.listar_clases(conn)
        choques = []
        for campo, etiqueta in (("profesor_id", "Profesor"), ("grupo_id", "Grupo"), ("salon_id", "Aula")):
            vistos = {}
            for c in clases:
                k = (c["dia"], c["franja_id"], c[campo])
                if k in vistos:
                    choques.append({"tipo": etiqueta, "clase": c, "otra": vistos[k]})
                else:
                    vistos[k] = c
        return render_template("conflictos.html", choques=choques, dias=servicio.DIAS)

    # ----- Editor visual: mover clases desde la cuadrícula -----
    @app.route("/admin/clase/<int:cid>/mover", methods=["POST"])
    @requiere("rector")
    def mover_clase(cid):
        clase_actual = servicio.obtener_clase(db(), cid)
        servicio.actualizar_clase(db(), cid, clase_actual["materia_id"], clase_actual["profesor_id"],
                                  clase_actual["grupo_id"], clase_actual["salon_id"],
                                  int(request.form["dia"]), int(request.form["franja_id"]))
        db().execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",
                     (session["usuario"], "Movimiento de clase", f"Clase {cid}: {servicio.DIAS[int(request.form['dia'])]}"))
        db().commit()
        flash("Clase reubicada correctamente")
        return redirect(request.referrer or url_for("horario"))

    # ----- Gestión docente: carga, ausencias, historial y respaldos -----
    @app.route("/admin/docentes")
    @requiere("rector")
    def gestion_docente():
        conn = db()
        historial = conn.execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT 100").fetchall()
        versiones = conn.execute(
            "SELECT id,fecha,usuario,descripcion FROM versiones_horario ORDER BY id DESC LIMIT 30").fetchall()
        return render_template("gestion_docente.html", historial=historial, versiones=versiones)

    def anotar(accion, detalle):
        db().execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",
                     (session["usuario"], accion, detalle))
        db().commit()

    @app.route("/admin/ausencias", methods=["GET", "POST"])
    @requiere("rector")
    def ausencias():
        conn = db()
        if request.method == "POST":
            f = request.form
            try:
                profesor_id = int(f["profesor_id"])
            except (KeyError, ValueError):
                raise servicio.ErrorDeNegocio("Elige un profesor")
            n = servicio.registrar_ausencia(conn, profesor_id, f.get("desde"),
                                            f.get("hasta") or f.get("desde"),
                                            f.get("motivo"), f.get("observaciones", ""))
            anotar("Ausencia registrada", f"Profesor {profesor_id} · desde {f.get('desde')} ({n} día(s))")
            flash(f"Ausencia registrada ({n} día(s) hábil(es)). Ahora asigna quién cubre sus clases.")
            return redirect(url_for("ausencias"))
        return render_template("ausencias.html", proximas=servicio.ausencias_con_clases(conn),
                               pasadas=servicio.ausencias_pasadas(conn),
                               profesores=servicio.listar_catalogo(conn, "profesores"),
                               dias=servicio.DIAS, hoy=date.today().isoformat())

    @app.route("/admin/ausencias/<int:aid>/eliminar", methods=["POST"])
    @requiere("rector")
    def quitar_ausencia(aid):
        servicio.eliminar_ausencia(db(), aid)
        anotar("Ausencia eliminada", f"Ausencia {aid}")
        flash("Ausencia eliminada, junto con sus reemplazos")
        return redirect(url_for("ausencias"))

    @app.route("/admin/reemplazos", methods=["POST"])
    @requiere("rector")
    def crear_reemplazo():
        f = request.form
        servicio.asignar_reemplazo(db(), int(f["ausencia_id"]), int(f["clase_id"]), int(f["profesor_id"]))
        anotar("Reemplazo asignado", f"Clase {f['clase_id']} · profesor {f['profesor_id']}")
        flash("Reemplazo asignado")
        return redirect(url_for("ausencias"))

    @app.route("/admin/reemplazos/<int:rid>/quitar", methods=["POST"])
    @requiere("rector")
    def quitar_reemplazo(rid):
        servicio.quitar_reemplazo(db(), rid)
        anotar("Reemplazo quitado", f"Reemplazo {rid}")
        flash("Reemplazo quitado")
        return redirect(url_for("ausencias"))

    @app.route("/cuenta", methods=["GET", "POST"])
    @requiere()
    def cuenta():
        if request.method == "POST":
            f = request.form
            if f["nueva"] != f["repetir"]:
                raise servicio.ErrorDeNegocio("Las contraseñas nuevas no coinciden")
            usuarios.cambiar_clave(db(), session["usuario"], f["actual"], f["nueva"])
            flash("Contraseña actualizada")
            return redirect(url_for("cuenta"))
        return render_template("cuenta.html")

    @app.route("/admin/versiones/guardar",methods=["POST"])
    @requiere("rector")
    def guardar_version():
        conn=db(); datos=[dict(r) for r in conn.execute("SELECT materia_id,profesor_id,grupo_id,salon_id,dia,franja_id FROM clases ORDER BY id")]
        conn.execute("INSERT INTO versiones_horario(usuario,descripcion,datos) VALUES(?,?,?)",(session["usuario"],request.form.get("descripcion") or "Respaldo manual",json.dumps(datos)))
        conn.execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",(session["usuario"],"Respaldo creado",request.form.get("descripcion") or "Respaldo manual"))
        conn.commit(); flash("Versión del horario guardada")
        return redirect(url_for("gestion_docente"))

    @app.route("/admin/versiones/<int:vid>/restaurar",methods=["POST"])
    @requiere("rector")
    def restaurar_version(vid):
        conn=db(); v=conn.execute("SELECT * FROM versiones_horario WHERE id=?",(vid,)).fetchone()
        if not v: raise servicio.NoEncontrado("No existe esa versión")
        datos=json.loads(v["datos"])
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM clases")
            conn.executemany("INSERT INTO clases(materia_id,profesor_id,grupo_id,salon_id,dia,franja_id) VALUES(:materia_id,:profesor_id,:grupo_id,:salon_id,:dia,:franja_id)",datos)
            conn.execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",(session["usuario"],"Horario restaurado",f"Versión {vid}: {v['descripcion']}"))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        flash("Horario restaurado desde la versión guardada")
        return redirect(url_for("gestion_docente"))

    @app.route("/admin/backup",methods=["POST"])
    @requiere("rector")
    def backup():
        origen=conectar(app.config["RUTA_DB"])
        carpeta=Path(app.config["RUTA_DB"]).resolve().parent/"respaldos"
        carpeta.mkdir(exist_ok=True)
        destino=carpeta/f"horarios_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        copia=sqlite3.connect(destino)
        origen.backup(copia); copia.close(); origen.close()
        db().execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",(session["usuario"],"Copia de seguridad",destino.name)); db().commit()
        flash(f"Copia de seguridad creada: respaldos/{destino.name}")
        return redirect(url_for("gestion_docente"))

    # ----- Administración (solo rector) -----
    @app.route("/clase/<int:cid>", methods=["GET", "POST"])
    @requiere("rector")
    def clase(cid):
        conn = db()
        if request.method == "POST":
            if request.form["accion"] == "eliminar":
                servicio.eliminar_clase(conn, cid)
                conn.execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",(session["usuario"],"Clase eliminada",f"Clase {cid}")); conn.commit()
                flash("Clase eliminada")
                return redirect(url_for("horario"))
            datos = {k: int(request.form[k]) for k in CAMPOS}
            c = (servicio.actualizar_clase(conn, cid, **datos) if cid
                 else servicio.crear_clase(conn, **datos))
            db().execute("INSERT INTO auditoria(usuario,accion,detalle) VALUES(?,?,?)",
                         (session["usuario"], "Clase guardada", f"{c['materia']} · {c['grupo']} · {c['profesor']}"))
            db().commit()
            flash("Clase guardada")
            return redirect(url_for("horario", ver=f"grupo:{c['grupo_id']}"))
        c = (servicio.obtener_clase(conn, cid) if cid
             else {k: request.args.get(k, type=int) for k in CAMPOS})
        selects = [
            ("materia_id", "Materia", servicio.listar_catalogo(conn, "materias")),
            ("profesor_id", "Profesor", servicio.listar_catalogo(conn, "profesores")),
            ("grupo_id", "Grupo", servicio.listar_catalogo(conn, "grupos")),
            ("salon_id", "Salón", servicio.listar_catalogo(conn, "salones")),
            ("dia", "Día", [{"id": n, "nombre": d.capitalize()} for n, d in enumerate(servicio.DIAS)]),
            ("franja_id", "Franja", [{"id": f["id"], "nombre": f"{f['inicio']} - {f['fin']}"}
                                     for f in servicio.listar_franjas(conn) if f["tipo"] == "clase"]),
        ]
        mats_prof = {p: [m["id"] for m in ms]
                     for p, ms in servicio.materias_por_profesor(conn).items()}
        return render_template("clase.html", c=c, cid=cid, selects=selects,
                               mats_prof=mats_prof)

    @app.route("/admin/personalizar", methods=["GET", "POST"])
    @requiere("rector")
    def personalizar():
        conn = db()
        if request.method == "POST":
            f = request.form
            accion = f.get("accion", "guardar")
            if accion == "quitar_logo":
                conn.execute("DELETE FROM configuracion WHERE clave = 'logo'")
            elif accion == "quitar_color":
                conn.execute("DELETE FROM configuracion WHERE clave = 'color'")
            else:
                color = (f.get("color") or "").strip()
                if color:
                    if not COLOR_HEX.fullmatch(color):
                        raise servicio.ErrorDeNegocio("El color no es válido")
                    conn.execute("INSERT OR REPLACE INTO configuracion(clave, valor) VALUES('color', ?)", (color.lower(),))
                archivo = request.files.get("logo")
                if archivo and archivo.filename:
                    datos = archivo.read(300_001)
                    if len(datos) > 300_000:
                        raise servicio.ErrorDeNegocio("El logo pesa más de 300 KB")
                    tipo = _tipo_imagen(datos)
                    if not tipo:
                        raise servicio.ErrorDeNegocio("El logo debe ser una imagen PNG, JPG, WEBP o GIF")
                    uri = f"data:{tipo};base64," + base64.b64encode(datos).decode()
                    conn.execute("INSERT OR REPLACE INTO configuracion(clave, valor) VALUES('logo', ?)", (uri,))
            conn.commit()
            flash("Personalización guardada")
            return redirect(url_for("personalizar"))
        return render_template("personalizar.html")

    @app.route("/admin/<tabla>", methods=["GET", "POST"])
    @requiere("rector")
    def admin(tabla):
        if tabla not in TABLAS:
            abort(404)
        conn = db()
        if request.method == "POST":
            f = request.form
            if tabla == "franjas":
                servicio.crear_franja(conn, None, f["inicio"], f["fin"], f.get("tipo", "clase"))
            else:
                r = servicio.crear_catalogo(conn, tabla, f["nombre"])
                if tabla == "profesores":
                    servicio.asignar_materias(
                        conn, r["id"], [int(x) for x in f.getlist("materias")])
                if tabla == "profesores" and f.get("usuario") and f.get("clave"):
                    usuarios.crear_usuario(conn, f["usuario"], f["clave"], "profesor", r["id"])
            flash("Agregado")
            return redirect(url_for("admin", tabla=tabla))
        if tabla == "salones":
            return redirect(url_for("aulas"))
        filas = (servicio.listar_franjas(conn) if tabla == "franjas"
                 else servicio.listar_catalogo(conn, tabla))
        extra = {}
        if tabla == "profesores":
            extra = {"accesos": {r["profesor_id"]: r["usuario"] for r in conn.execute(
                         "SELECT profesor_id, usuario FROM usuarios WHERE profesor_id IS NOT NULL")},
                     "horas": {r[0]: r[1] for r in conn.execute(
                         "SELECT profesor_id, COUNT(*) FROM clases GROUP BY profesor_id")},
                     "materias": servicio.listar_catalogo(conn, "materias"),
                     "mats": servicio.materias_por_profesor(conn)}
        elif tabla == "materias":
            extra = {"quienes": {}}
            for pid, ms in servicio.materias_por_profesor(conn).items():
                nombre = conn.execute("SELECT nombre FROM profesores WHERE id = ?", (pid,)).fetchone()["nombre"]
                for m in ms:
                    extra["quienes"].setdefault(m["id"], []).append(nombre)
        elif tabla == "grupos":
            extra = {"aula_de": {g["grupo_id"]: g["salon"] for g in servicio.asignaciones_aulas(conn)[0]}}
        elif tabla == "franjas":
            for f in filas:
                h1, m1 = map(int, f["inicio"].split(":"))
                h2, m2 = map(int, f["fin"].split(":"))
                f["dur"] = (h2 * 60 + m2) - (h1 * 60 + m1)
        return render_template("admin.html", tabla=tabla, filas=filas, **extra)

    @app.route("/admin/<tabla>/<int:id>/eliminar", methods=["POST"])
    @requiere("rector")
    def eliminar(tabla, id):
        if tabla not in TABLAS:
            abort(404)
        servicio.eliminar_registro(db(), tabla, id)
        flash("Eliminado")
        return redirect(url_for("aulas") if tabla == "salones" else url_for("admin", tabla=tabla))

    @app.route("/admin/franjas/<int:fid>/editar", methods=["POST"])
    @requiere("rector")
    def editar_franja(fid):
        f = request.form
        servicio.actualizar_franja(db(), fid, None, f["inicio"], f["fin"], f.get("tipo", "clase"))
        flash("Franja actualizada")
        return redirect(url_for("admin", tabla="franjas"))

    @app.route("/admin/profesores/<int:pid>/materias", methods=["POST"])
    @requiere("rector")
    def materias_prof(pid):
        servicio.asignar_materias(db(), pid, [int(x) for x in request.form.getlist("materias")])
        flash("Materias guardadas")
        return redirect(url_for("admin", tabla="profesores"))

    @app.route("/admin/profesores/<int:pid>/acceso", methods=["POST"])
    @requiere("rector")
    def acceso(pid):
        usuarios.establecer_acceso(db(), pid, request.form.get("usuario"), request.form["clave"])
        flash("Acceso guardado")
        return redirect(url_for("admin", tabla="profesores"))

    return app
