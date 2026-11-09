import os
import tempfile
import unittest
from datetime import datetime

from app import servicio as s
from app import usuarios
from app.db import conectar, iniciar_db

try:
    from app.web import crear_app
except ImportError:  # Flask no instalado
    crear_app = None

LUNES = "2026-11-02"


def preparar(conn):
    """Datos mínimos: 3 profesores, 1 grupo, 2 franjas de clase y un descanso."""
    d = {}
    for nombre in ("Ana", "Luis", "Marta"):
        d[nombre] = s.crear_catalogo(conn, "profesores", nombre)["id"]
    d["mate"] = s.crear_catalogo(conn, "materias", "Matemáticas")["id"]
    d["fisica"] = s.crear_catalogo(conn, "materias", "Física")["id"]
    d["g"] = s.crear_catalogo(conn, "grupos", "6A")["id"]
    d["salon"] = s.crear_catalogo(conn, "salones", "101")["id"]
    d["f1"] = s.crear_franja(conn, None, "07:00", "08:00")["id"]
    d["rec"] = s.crear_franja(conn, None, "08:00", "08:20", "descanso")["id"]
    d["f2"] = s.crear_franja(conn, None, "08:20", "09:20")["id"]
    s.asignar_materias(conn, d["Ana"], [d["mate"]])
    s.asignar_materias(conn, d["Luis"], [d["mate"], d["fisica"]])
    s.asignar_materias(conn, d["Marta"], [d["fisica"]])
    return d


class PruebasFranjas(unittest.TestCase):
    def setUp(self):
        self.conn = conectar(":memory:")
        iniciar_db(self.conn)
        self.d = preparar(self.conn)

    def test_franjas_ordenadas_por_hora_con_descanso(self):
        tipos = [(f["inicio"], f["tipo"]) for f in s.listar_franjas(self.conn)]
        self.assertEqual(tipos, [("07:00", "clase"), ("08:00", "descanso"), ("08:20", "clase")])

    def test_no_se_permiten_clases_en_descanso(self):
        with self.assertRaises(s.ErrorDeNegocio):
            s.crear_clase(self.conn, self.d["mate"], self.d["Ana"], self.d["g"],
                          self.d["salon"], 0, self.d["rec"])

    def test_franjas_que_se_cruzan(self):
        with self.assertRaises(s.Conflicto):
            s.crear_franja(self.conn, None, "07:30", "08:30")

    def test_no_convierte_en_descanso_si_tiene_clases(self):
        s.crear_clase(self.conn, self.d["mate"], self.d["Ana"], self.d["g"],
                      self.d["salon"], 0, self.d["f1"])
        with self.assertRaises(s.Conflicto):
            s.actualizar_franja(self.conn, self.d["f1"], None, "07:00", "08:00", "descanso")

    def test_estado_ahora_en_descanso(self):
        e = s.estado_ahora(self.conn, datetime(2026, 11, 2, 8, 10))
        self.assertTrue(e["descanso"])
        self.assertEqual(e["en_clase"], [])

    def test_libres_en_descanso_es_vacio(self):
        self.assertEqual(s.profesores_libres(self.conn, 0, self.d["rec"]), [])


class PruebasAusencias(unittest.TestCase):
    def setUp(self):
        self.conn = conectar(":memory:")
        iniciar_db(self.conn)
        self.d = preparar(self.conn)
        self.clase = s.crear_clase(self.conn, self.d["mate"], self.d["Ana"], self.d["g"],
                                   self.d["salon"], 0, self.d["f1"])

    def test_rango_solo_cuenta_dias_habiles(self):
        # viernes 6 a lunes 9 de noviembre: viernes y lunes
        n = s.registrar_ausencia(self.conn, self.d["Ana"], "2026-11-06", "2026-11-09", "Incapacidad")
        self.assertEqual(n, 2)

    def test_ausencia_repetida_o_en_fin_de_semana(self):
        s.registrar_ausencia(self.conn, self.d["Ana"], LUNES, LUNES, "Permiso")
        with self.assertRaises(s.ErrorDeNegocio):
            s.registrar_ausencia(self.conn, self.d["Ana"], LUNES, LUNES, "Permiso")
        with self.assertRaises(s.ErrorDeNegocio):
            s.registrar_ausencia(self.conn, self.d["Ana"], "2026-11-07", "2026-11-08", "Permiso")

    def test_fechas_invalidas(self):
        with self.assertRaises(s.ErrorDeNegocio):
            s.registrar_ausencia(self.conn, self.d["Ana"], "no-es-fecha", None, "Otro")
        with self.assertRaises(s.ErrorDeNegocio):
            s.registrar_ausencia(self.conn, self.d["Ana"], "2026-11-05", "2026-11-02", "Otro")

    def _ausencia(self):
        s.registrar_ausencia(self.conn, self.d["Ana"], LUNES, LUNES, "Permiso")
        return self.conn.execute("SELECT id FROM ausencias").fetchone()["id"]

    def test_candidatos_primero_quien_dicta_la_materia(self):
        self._ausencia()
        a = s.ausencias_con_clases(self.conn, desde=LUNES)[0]
        nombres = [(p["nombre"], p["ensenia"]) for p in a["clases"][0]["candidatos"]]
        self.assertEqual(nombres, [("Luis", True), ("Marta", False)])  # Ana (ausente) no sale

    def test_reemplazo_y_estado_ahora(self):
        aid = self._ausencia()
        # cualquier profesor libre puede cubrir, aunque no dicte la materia
        s.asignar_reemplazo(self.conn, aid, self.clase["id"], self.d["Marta"])
        e = s.estado_ahora(self.conn, datetime(2026, 11, 2, 7, 30))
        self.assertEqual(e["en_clase"][0]["reemplazo"], "Marta")
        libres = [p["nombre"] for p in e["libres"]]
        self.assertEqual(libres, ["Luis"])  # Ana ausente y Marta cubriendo

    def test_ausente_sin_reemplazo_se_marca(self):
        self._ausencia()
        e = s.estado_ahora(self.conn, datetime(2026, 11, 2, 7, 30))
        self.assertTrue(e["en_clase"][0]["ausente"])
        self.assertIsNone(e["en_clase"][0]["reemplazo"])

    def test_reemplazo_invalido(self):
        aid = self._ausencia()
        with self.assertRaises(s.ErrorDeNegocio):  # el mismo ausente
            s.asignar_reemplazo(self.conn, aid, self.clase["id"], self.d["Ana"])
        otra = s.crear_clase(self.conn, self.d["fisica"], self.d["Luis"], self.d["g"],
                             self.d["salon"], 0, self.d["f2"])
        with self.assertRaises(s.ErrorDeNegocio):  # clase de otro profesor
            s.asignar_reemplazo(self.conn, aid, otra["id"], self.d["Marta"])
        s.asignar_reemplazo(self.conn, aid, self.clase["id"], self.d["Marta"])
        # Luis tiene clase en f2; Marta ya cubre f1: no puede cubrir dos a la vez
        g2 = s.crear_catalogo(self.conn, "grupos", "7A")["id"]
        sal2 = s.crear_catalogo(self.conn, "salones", "102")["id"]
        clase_b = s.crear_clase(self.conn, self.d["mate"], self.d["Luis"], g2, sal2, 0, self.d["f1"])
        s.registrar_ausencia(self.conn, self.d["Luis"], LUNES, LUNES, "Permiso")
        aid_luis = self.conn.execute("SELECT id FROM ausencias WHERE profesor_id=?",
                                     (self.d["Luis"],)).fetchone()["id"]
        with self.assertRaises(s.Conflicto):
            s.asignar_reemplazo(self.conn, aid_luis, clase_b["id"], self.d["Marta"])

    def test_eliminar_ausencia_quita_sus_reemplazos(self):
        aid = self._ausencia()
        s.asignar_reemplazo(self.conn, aid, self.clase["id"], self.d["Marta"])
        s.eliminar_ausencia(self.conn, aid)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reemplazos").fetchone()[0], 0)

    def test_quitar_reemplazo(self):
        aid = self._ausencia()
        s.asignar_reemplazo(self.conn, aid, self.clase["id"], self.d["Marta"])
        rid = self.conn.execute("SELECT id FROM reemplazos").fetchone()["id"]
        s.quitar_reemplazo(self.conn, rid)
        with self.assertRaises(s.NoEncontrado):
            s.quitar_reemplazo(self.conn, rid)


class PruebasGenerador(unittest.TestCase):
    def setUp(self):
        self.conn = conectar(":memory:")
        iniciar_db(self.conn)
        self.d = preparar(self.conn)
        # franja extra para tener 4 de clase
        s.crear_franja(self.conn, None, "09:20", "10:20")
        s.crear_franja(self.conn, None, "10:20", "11:20")
        self.salon2 = s.crear_catalogo(self.conn, "salones", "102")["id"]
        self.g2 = s.crear_catalogo(self.conn, "grupos", "7A")["id"]
        s.asignar_salon_grupo(self.conn, self.d["g"], self.d["salon"])
        s.asignar_salon_grupo(self.conn, self.g2, self.salon2)
        for g in (self.d["g"], self.g2):
            s.guardar_carga(self.conn, g, self.d["mate"], 3)
            s.guardar_carga(self.conn, g, self.d["fisica"], 3)

    def test_genera_aunque_haya_horario_y_una_operacion_pendiente(self):
        # Antes fallaba con "cannot start a transaction within a transaction"
        s.generar_horario(self.conn)
        self.conn.execute("INSERT INTO versiones_horario(usuario,descripcion,datos) VALUES('t','x','[]')")
        total = s.generar_horario(self.conn)
        self.assertEqual(total, 12)
        # la versión que estaba pendiente quedó guardada
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM versiones_horario").fetchone()[0], 1)

    def test_no_pone_clases_en_descansos_ni_cruces(self):
        s.generar_horario(self.conn)
        en_descanso = self.conn.execute(
            "SELECT COUNT(*) FROM clases c JOIN franjas f ON f.id=c.franja_id WHERE f.tipo='descanso'"
        ).fetchone()[0]
        self.assertEqual(en_descanso, 0)
        self.assertEqual(s.estadisticas(self.conn)["conflictos"], 0)

    def test_reparte_la_materia_en_dias_distintos(self):
        s.generar_horario(self.conn)
        repetidas = self.conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM clases GROUP BY grupo_id, materia_id, dia HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        self.assertEqual(repetidas, 0)


class PruebasAulas(unittest.TestCase):
    def test_cambiar_tipo_limpia_asignaciones(self):
        conn = conectar(":memory:")
        iniciar_db(conn)
        d = preparar(conn)
        s.asignar_salon_grupo(conn, d["g"], d["salon"])
        s.cambiar_tipo_aula(conn, d["salon"], "especial")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM grupo_salon").fetchone()[0], 0)
        s.asignar_materias_salon(conn, d["salon"], [d["mate"]])
        s.cambiar_tipo_aula(conn, d["salon"], "principal")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM salon_materias").fetchone()[0], 0)
        with self.assertRaises(s.ErrorDeNegocio):
            s.cambiar_tipo_aula(conn, d["salon"], "otro")


class PruebasCuenta(unittest.TestCase):
    def test_cambiar_clave(self):
        conn = conectar(":memory:")
        iniciar_db(conn)
        usuarios.crear_usuario(conn, "rector", "clave123", "rector")
        with self.assertRaises(s.ErrorDeNegocio):
            usuarios.cambiar_clave(conn, "rector", "equivocada", "nueva123")
        with self.assertRaises(s.ErrorDeNegocio):
            usuarios.cambiar_clave(conn, "rector", "clave123", "corta")
        usuarios.cambiar_clave(conn, "rector", "clave123", "nueva123")
        self.assertIsNone(usuarios.autenticar(conn, "rector", "clave123"))
        self.assertIsNotNone(usuarios.autenticar(conn, "rector", "nueva123"))


@unittest.skipUnless(crear_app, "Flask no está instalado")
class PruebasWebMejoras(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = crear_app(self.ruta)
        self.c = self.app.test_client()
        self.c.post("/configurar", data={"usuario": "rector", "clave": "rector123"})
        self.c.post("/login", data={"usuario": "rector", "clave": "rector123"})
        conn = conectar(self.ruta)
        self.d = preparar(conn)
        self.clase = s.crear_clase(conn, self.d["mate"], self.d["Ana"], self.d["g"],
                                   self.d["salon"], 0, self.d["f1"])
        conn.close()

    def tearDown(self):
        os.remove(self.ruta)

    def test_paginas_nuevas_cargan(self):
        for ruta in ("/admin/ausencias", "/cuenta", "/admin/franjas", "/admin/docentes",
                     "/horario?ver=grupo:1", "/libres", "/"):
            self.assertEqual(self.c.get(ruta).status_code, 200, ruta)

    def test_el_horario_muestra_el_descanso(self):
        h = self.c.get("/horario?ver=grupo:1").get_data(as_text=True)
        self.assertEqual(h.count('class="descanso"'), 1)

    def test_crear_franja_de_descanso_desde_la_pagina(self):
        r = self.c.post("/admin/franjas", data={"inicio": "09:20", "fin": "09:40", "tipo": "descanso"},
                        follow_redirects=True)
        self.assertIn("Agregado", r.get_data(as_text=True))
        r = self.c.post("/admin/franjas", data={"inicio": "09:30", "fin": "10:00", "tipo": "clase"},
                        follow_redirects=True)
        self.assertIn("Se cruza con la franja", r.get_data(as_text=True))

    def test_flujo_de_ausencia_y_reemplazo(self):
        r = self.c.post("/admin/ausencias", data={"profesor_id": self.d["Ana"], "desde": LUNES,
                                                 "hasta": LUNES, "motivo": "Permiso"},
                        follow_redirects=True)
        self.assertIn("Ausencia registrada", r.get_data(as_text=True))
        aid = 1
        r = self.c.post("/admin/reemplazos", data={"ausencia_id": aid, "clase_id": self.clase["id"],
                                                  "profesor_id": self.d["Marta"]},
                        follow_redirects=True)
        self.assertIn("Reemplazo asignado", r.get_data(as_text=True))
        r = self.c.post("/admin/reemplazos/1/quitar", follow_redirects=True)
        self.assertIn("Reemplazo quitado", r.get_data(as_text=True))
        r = self.c.post("/admin/ausencias/1/eliminar", follow_redirects=True)
        self.assertIn("Ausencia eliminada", r.get_data(as_text=True))

    def test_imprimir_horario_del_rector_incluye_el_descanso(self):
        h = self.c.get("/horario/imprimir?ver=grupo:1")
        self.assertEqual(h.status_code, 200)
        html = h.get_data(as_text=True)
        self.assertIn("Horario del grupo 6A", html)
        self.assertEqual(html.count('class="desc"'), 1)
        self.assertEqual(self.c.get("/horario/imprimir?ver=grupo:99").status_code, 404)
        self.assertEqual(self.c.get("/horario/imprimir").status_code, 404)

    def test_menu_marca_la_pagina_activa_y_no_tiene_salones(self):
        html = self.c.get("/admin/profesores").get_data(as_text=True)
        self.assertIn('class="item activo"', html)
        self.assertNotIn(">Salones<", html)
        self.assertIn("Respaldos e historial", html)

    def test_cambiar_tipo_de_aula_desde_la_pagina(self):
        r = self.c.post("/admin/aulas", data={"accion": "tipo", "salon_id": self.d["salon"],
                                              "tipo": "especial"}, follow_redirects=True)
        self.assertIn("Tipo de aula actualizado", r.get_data(as_text=True))
        conn = conectar(self.ruta)
        self.assertEqual(conn.execute("SELECT tipo FROM salones WHERE id=?", (self.d["salon"],)).fetchone()[0],
                         "especial")
        conn.close()

    def test_inicio_del_rector_muestra_la_lista_de_pasos(self):
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("Antes de generar el horario", html)
        self.assertIn("Ausencias hoy", html)

    def test_cambiar_clave_desde_la_cuenta(self):
        r = self.c.post("/cuenta", data={"actual": "rector123", "nueva": "otra12345",
                                         "repetir": "otra12345"}, follow_redirects=True)
        self.assertIn("Contraseña actualizada", r.get_data(as_text=True))
        r = self.c.post("/cuenta", data={"actual": "otra12345", "nueva": "abcdef1",
                                         "repetir": "distinta"}, follow_redirects=True)
        self.assertIn("no coinciden", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
