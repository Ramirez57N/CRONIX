import base64
import io
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from app.web import crear_app
except ImportError:  # Flask no instalado
    crear_app = None

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@unittest.skipUnless(crear_app, "Flask no está instalado")
class PruebasPlataforma(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.app = crear_app(self.dir / "horarios.db")
        self.dueno = self.app.test_client()
        self.dueno.post("/propietario/configurar", data={"usuario": "dueno", "clave": "clavelarga123"})
        self.dueno.post("/ingresar", data={"tipo": "propietario", "usuario": "dueno", "clave": "clavelarga123"})
        self.dueno.post("/propietario", data={"nombre": "Colegio Prueba", "codigo": "prueba",
                                              "usuario": "rector1", "clave": "rector123"})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entrar(self, tipo, usuario, clave, codigo="prueba"):
        c = self.app.test_client()
        r = c.post("/ingresar", data={"tipo": tipo, "codigo": codigo, "usuario": usuario, "clave": clave},
                   follow_redirects=True)
        return c, r.get_data(as_text=True)

    def test_ingreso_unico_por_tipo(self):
        _, html = self.entrar("rector", "rector1", "rector123")
        self.assertIn("Bienvenido a Colegio Prueba", html)
        _, html = self.entrar("profesor", "rector1", "rector123")   # tipo que no corresponde
        self.assertIn("Datos incorrectos", html)
        _, html = self.entrar("rector", "rector1", "mala")
        self.assertIn("Datos incorrectos", html)
        _, html = self.entrar("rector", "rector1", "rector123", codigo="otro")
        self.assertIn("Datos incorrectos", html)
        c, html = self.entrar("propietario", "dueno", "clavelarga123", codigo="")
        self.assertIn("Consola del propietario", html)

    def test_rutas_viejas_llevan_al_ingreso(self):
        c = self.app.test_client()
        for ruta in ("/colegio", "/login", "/propietario/login"):
            r = c.get(ruta)
            self.assertEqual(r.status_code, 302, ruta)
            self.assertTrue(r.headers["Location"].endswith("/ingresar"), ruta)

    def test_menu_con_nombre_y_soporte_sin_decir_rector(self):
        self.dueno.post("/propietario/entrar/prueba")
        html = self.dueno.get("/").get_data(as_text=True)
        self.assertIn("Colegio Prueba", html)
        self.assertIn("Propietario · soporte", html)
        self.assertNotIn("<small>Rector</small>", html)
        _, html = self.entrar("rector", "rector1", "rector123")
        self.assertIn("<small>Rector</small>", html)

    def test_propietario_ve_y_restablece_rectores(self):
        html = self.dueno.get("/propietario/prueba/rectores").get_data(as_text=True)
        self.assertIn("rector1", html)
        self.dueno.post("/propietario/prueba/rectores/1/clave", data={"clave": "nueva1234"})
        _, html = self.entrar("rector", "rector1", "rector123")
        self.assertIn("Datos incorrectos", html)
        _, html = self.entrar("rector", "rector1", "nueva1234")
        self.assertIn("Bienvenido a Colegio Prueba", html)
        self.dueno.post("/propietario/prueba/rectores", data={"usuario": "rector2", "clave": "otra12345"})
        _, html = self.entrar("rector", "rector2", "otra12345")
        self.assertIn("Bienvenido a Colegio Prueba", html)
        auditoria = self.dueno.get("/propietario/auditoria").get_data(as_text=True)
        self.assertIn("Contraseña restablecida al rector: rector1", auditoria)
        self.assertIn("Rector creado: rector2", auditoria)

    def test_rectores_solo_para_el_propietario(self):
        c, _ = self.entrar("rector", "rector1", "rector123")
        for ruta in ("/propietario/prueba/rectores", "/propietario/auditoria"):
            self.assertEqual(c.get(ruta).status_code, 302, ruta)
        self.assertEqual(c.post("/propietario/prueba/rectores/1/clave", data={"clave": "hackeada1"}).status_code, 302)
        _, html = self.entrar("rector", "rector1", "rector123")
        self.assertIn("Bienvenido", html)

    def test_resumen_y_ultimo_ingreso(self):
        html = self.dueno.get("/propietario").get_data(as_text=True)
        self.assertIn("Nunca", html)
        c, _ = self.entrar("rector", "rector1", "rector123")
        c.post("/admin/profesores", data={"nombre": "Ana"})
        html = self.dueno.get("/propietario").get_data(as_text=True)
        self.assertNotIn("Nunca", html.split("Instituciones registradas")[1].split("Institución actual")[0])

    def test_copia_de_seguridad_por_colegio(self):
        self.dueno.post("/propietario/respaldo/prueba")
        copias = list((self.dir / "respaldos_plataforma").glob("prueba-*.db"))
        self.assertEqual(len(copias), 1)
        self.assertIn("Copia de seguridad creada", self.dueno.get("/propietario/auditoria").get_data(as_text=True))

    def test_aviso_de_claves_iniciales(self):
        c, _ = self.entrar("rector", "rector1", "rector123")
        c.post("/admin/profesores", data={"nombre": "Ana Pérez", "usuario": "ana", "clave": "123456"})
        self.assertIn("Claves iniciales sin cambiar", c.get("/").get_data(as_text=True))
        p, _ = self.entrar("profesor", "ana", "123456")
        p.post("/cuenta", data={"actual": "123456", "nueva": "miclave99", "repetir": "miclave99"})
        self.assertNotIn("Claves iniciales sin cambiar", c.get("/").get_data(as_text=True))

    def test_personalizar_color_y_logo(self):
        c, _ = self.entrar("rector", "rector1", "rector123")
        c.post("/admin/personalizar", data={"color": "#aa3300", "logo": (io.BytesIO(PNG), "logo.png")},
               content_type="multipart/form-data")
        html = c.get("/").get_data(as_text=True)
        self.assertIn("--azul:#aa3300", html)
        self.assertIn("data:image/png;base64,", html)
        # Un archivo que no es imagen se rechaza aunque se llame .png
        c.post("/admin/personalizar", data={"logo": (io.BytesIO(b"<script>alert(1)</script>"), "x.png")},
               content_type="multipart/form-data")
        self.assertNotIn("alert(1)", c.get("/").get_data(as_text=True))
        # Un color inválido no se guarda
        c.post("/admin/personalizar", data={"color": "red;}</style><script>"}, content_type="multipart/form-data")
        self.assertNotIn("<script>", c.get("/").get_data(as_text=True))
        c.post("/admin/personalizar", data={"accion": "quitar_logo"})
        c.post("/admin/personalizar", data={"accion": "quitar_color"})
        html = c.get("/").get_data(as_text=True)
        self.assertNotIn("data:image/png", html)
        self.assertNotIn("--azul:#aa3300", html)

    def test_profesor_no_personaliza(self):
        c, _ = self.entrar("rector", "rector1", "rector123")
        c.post("/admin/profesores", data={"nombre": "Ana", "usuario": "ana", "clave": "ana12345"})
        p, _ = self.entrar("profesor", "ana", "ana12345")
        self.assertEqual(p.get("/admin/personalizar").status_code, 403)

    def test_colegios_no_comparten_personalizacion(self):
        self.dueno.post("/propietario", data={"nombre": "Otro Colegio", "codigo": "otro",
                                              "usuario": "rector-otro", "clave": "rector123"})
        c, _ = self.entrar("rector", "rector1", "rector123")
        c.post("/admin/personalizar", data={"color": "#aa3300"}, content_type="multipart/form-data")
        _, html = self.entrar("rector", "rector-otro", "rector123", codigo="otro")
        self.assertIn("Bienvenido a Otro Colegio", html)
        self.assertNotIn("#aa3300", html)

    def test_colegio_de_version_anterior_se_actualiza_solo(self):
        import sqlite3
        c0, _ = self.entrar("rector", "rector1", "rector123")
        c0.post("/admin/profesores", data={"nombre": "Ana Pérez", "usuario": "ana", "clave": "123456"})
        ruta = next((self.dir / "instituciones").glob("*.db"))
        viejo = sqlite3.connect(ruta)  # deja la base como la versión anterior: sin la columna ni la tabla nuevas
        viejo.execute("ALTER TABLE usuarios DROP COLUMN clave_inicial")
        viejo.execute("DROP TABLE configuracion")
        viejo.commit(); viejo.close()
        self.app = crear_app(self.dir / "horarios.db")  # arranque nuevo, como al reiniciar run.py
        self.dueno = self.app.test_client()
        self.dueno.post("/ingresar", data={"tipo": "propietario", "usuario": "dueno", "clave": "clavelarga123"})
        c, html = self.entrar("rector", "rector1", "rector123")
        self.assertIn("Bienvenido a Colegio Prueba", html)
        self.assertEqual(c.get("/admin/personalizar").status_code, 200)
        self.dueno.post("/propietario/prueba/rectores", data={"usuario": "rector2", "clave": "otra12345"})
        _, html = self.entrar("rector", "rector2", "otra12345")
        self.assertIn("Bienvenido a Colegio Prueba", html)


if __name__ == "__main__":
    unittest.main()
