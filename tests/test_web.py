import os
import tempfile
import unittest

try:
    from app.web import crear_app
except ImportError:  # Flask no instalado
    crear_app = None


@unittest.skipUnless(crear_app, "Flask no está instalado")
class PruebasWeb(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = crear_app(self.ruta)
        self.rector = self.app.test_client()
        self.rector.post("/configurar", data={"usuario": "rector", "clave": "rector123"})
        self.rector.post("/login", data={"usuario": "rector", "clave": "rector123"})

    def tearDown(self):
        os.remove(self.ruta)

    def test_sin_sesion_redirige(self):
        r = self.app.test_client().get("/")
        self.assertEqual(r.status_code, 302)

    def test_clave_incorrecta(self):
        r = self.app.test_client().post(
            "/login", data={"usuario": "rector", "clave": "mala"}, follow_redirects=True)
        self.assertIn("incorrectos", r.get_data(as_text=True))

    def test_profesor_no_entra_a_admin(self):
        self.rector.post("/admin/profesores",
                         data={"nombre": "Ana", "usuario": "ana", "clave": "ana12345"})
        prof = self.app.test_client()
        prof.post("/login", data={"usuario": "ana", "clave": "ana12345"})
        self.assertEqual(prof.get("/").status_code, 200)
        self.assertEqual(prof.get("/horario").status_code, 200)
        self.assertEqual(prof.get("/admin/profesores").status_code, 403)

    def test_rector_crea_clase_y_detecta_choque(self):
        self.rector.post("/admin/materias", data={"nombre": "Mate"})
        self.rector.post("/admin/profesores", data={"nombre": "Ana", "materias": ["1"]})
        for t, n in [("grupos", "6A"), ("grupos", "6B"), ("salones", "101"), ("salones", "102")]:
            self.rector.post(f"/admin/{t}", data={"nombre": n})
        self.rector.post("/admin/franjas", data={"orden": 1, "inicio": "07:00", "fin": "08:00"})
        datos = dict(materia_id=1, profesor_id=1, grupo_id=1, salon_id=1, dia=0,
                     franja_id=1, accion="guardar")
        self.rector.post("/clase/0", data=datos)
        datos.update(grupo_id=2, salon_id=2)  # mismo profesor, misma hora
        r = self.rector.post("/clase/0", data=datos, follow_redirects=True)
        self.assertIn("ya tiene clase", r.get_data(as_text=True))

    def test_profesor_solo_da_sus_materias(self):
        self.rector.post("/admin/materias", data={"nombre": "Mate"})
        self.rector.post("/admin/materias", data={"nombre": "Física"})
        self.rector.post("/admin/profesores", data={"nombre": "Ana", "materias": ["1"]})
        self.rector.post("/admin/grupos", data={"nombre": "6A"})
        self.rector.post("/admin/salones", data={"nombre": "101"})
        self.rector.post("/admin/franjas", data={"orden": 1, "inicio": "07:00", "fin": "08:00"})
        datos = dict(materia_id=2, profesor_id=1, grupo_id=1, salon_id=1, dia=0,
                     franja_id=1, accion="guardar")
        r = self.rector.post("/clase/0", data=datos, follow_redirects=True)
        self.assertIn("no tiene asignada esa materia", r.get_data(as_text=True))
        self.rector.post("/admin/profesores/1/materias", data={"materias": ["1", "2"]})
        r = self.rector.post("/clase/0", data=datos, follow_redirects=True)
        self.assertIn("Clase guardada", r.get_data(as_text=True))

    def test_paginas_se_muestran(self):
        self.rector.post("/admin/materias", data={"nombre": "Mate"})
        self.rector.post("/admin/profesores", data={"nombre": "Ana", "materias": ["1"]})
        self.rector.post("/admin/grupos", data={"nombre": "6A"})
        self.rector.post("/admin/salones", data={"nombre": "101"})
        self.rector.post("/admin/franjas", data={"orden": 1, "inicio": "07:00", "fin": "08:00"})
        for ruta in ["/", "/horario?ver=grupo:1", "/libres", "/clase/0",
                     "/admin/profesores", "/admin/materias", "/admin/grupos",
                     "/admin/aulas", "/admin/franjas", "/admin/cargas", "/admin/conflictos"]:
            self.assertEqual(self.rector.get(ruta).status_code, 200, ruta)
        # Salones ya no tiene página propia: se gestiona en Aulas
        r = self.rector.get("/admin/salones")
        self.assertEqual((r.status_code, r.headers["Location"].endswith("/admin/aulas")), (302, True))
        self.assertIn("Mate", self.rector.get("/admin/profesores").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
