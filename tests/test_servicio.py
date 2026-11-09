import unittest
from datetime import datetime

from app import servicio as s
from app.db import conectar, iniciar_db


class PruebasHorario(unittest.TestCase):
    def setUp(self):
        self.conn = conectar(":memory:")
        iniciar_db(self.conn)
        self.ana = s.crear_catalogo(self.conn, "profesores", "Ana")["id"]
        self.luis = s.crear_catalogo(self.conn, "profesores", "Luis")["id"]
        self.mate = s.crear_catalogo(self.conn, "materias", "Matemáticas")["id"]
        self.g6a = s.crear_catalogo(self.conn, "grupos", "6A")["id"]
        self.g6b = s.crear_catalogo(self.conn, "grupos", "6B")["id"]
        self.s101 = s.crear_catalogo(self.conn, "salones", "101")["id"]
        self.s102 = s.crear_catalogo(self.conn, "salones", "102")["id"]
        self.f1 = s.crear_franja(self.conn, 1, "07:00", "08:00")["id"]
        self.f2 = s.crear_franja(self.conn, 2, "08:00", "09:00")["id"]
        s.asignar_materias(self.conn, self.ana, [self.mate])
        s.asignar_materias(self.conn, self.luis, [self.mate])

    def clase(self, prof, grupo, salon, dia=0, franja=None):
        return s.crear_clase(self.conn, self.mate, prof, grupo, salon, dia,
                             franja or self.f1)

    def test_crea_clase(self):
        c = self.clase(self.ana, self.g6a, self.s101)
        self.assertEqual(c["profesor"], "Ana")
        self.assertEqual(c["inicio"], "07:00")

    def test_choque_de_profesor(self):
        self.clase(self.ana, self.g6a, self.s101)
        with self.assertRaises(s.Conflicto):
            self.clase(self.ana, self.g6b, self.s102)

    def test_choque_de_grupo(self):
        self.clase(self.ana, self.g6a, self.s101)
        with self.assertRaises(s.Conflicto):
            self.clase(self.luis, self.g6a, self.s102)

    def test_choque_de_salon(self):
        self.clase(self.ana, self.g6a, self.s101)
        with self.assertRaises(s.Conflicto):
            self.clase(self.luis, self.g6b, self.s101)

    def test_mismo_recurso_en_otra_franja_es_valido(self):
        self.clase(self.ana, self.g6a, self.s101, franja=self.f1)
        self.clase(self.ana, self.g6a, self.s101, franja=self.f2)

    def test_actualizar_no_choca_consigo_misma(self):
        c = self.clase(self.ana, self.g6a, self.s101)
        nueva = s.actualizar_clase(self.conn, c["id"], self.mate, self.ana,
                                   self.g6a, self.s102, 0, self.f1)
        self.assertEqual(nueva["salon"], "102")

    def test_actualizar_con_choque(self):
        self.clase(self.ana, self.g6a, self.s101, franja=self.f1)
        c2 = self.clase(self.luis, self.g6b, self.s102, franja=self.f2)
        with self.assertRaises(s.Conflicto):
            # Ana ya da clase en f1; mover la clase de Luis a f1 con Ana choca
            s.actualizar_clase(self.conn, c2["id"], self.mate, self.ana,
                               self.g6b, self.s102, 0, self.f1)

    def test_profesores_libres(self):
        self.clase(self.ana, self.g6a, self.s101)
        libres = s.profesores_libres(self.conn, 0, self.f1)
        self.assertEqual([p["nombre"] for p in libres], ["Luis"])

    def test_estado_ahora(self):
        self.clase(self.ana, self.g6a, self.s101)
        lunes_730 = datetime(2026, 11, 2, 7, 30)  # lunes
        estado = s.estado_ahora(self.conn, lunes_730)
        self.assertEqual([c["profesor"] for c in estado["en_clase"]], ["Ana"])
        self.assertEqual([p["nombre"] for p in estado["libres"]], ["Luis"])

    def test_estado_ahora_fuera_de_horario(self):
        domingo = datetime(2026, 11, 8, 7, 30)
        self.assertEqual(s.estado_ahora(self.conn, domingo)["en_clase"], [])

    def test_profesor_sin_la_materia(self):
        otra = s.crear_catalogo(self.conn, "materias", "Física")["id"]
        with self.assertRaises(s.ErrorDeNegocio):
            s.crear_clase(self.conn, otra, self.ana, self.g6a, self.s101, 0, self.f1)

    def test_asignar_materias_reemplaza(self):
        otra = s.crear_catalogo(self.conn, "materias", "Física")["id"]
        s.asignar_materias(self.conn, self.ana, [otra])
        nombres = [m["nombre"] for m in s.materias_por_profesor(self.conn)[self.ana]]
        self.assertEqual(nombres, ["Física"])

    def test_eliminar_materia_asignada_sin_clases(self):
        otra = s.crear_catalogo(self.conn, "materias", "Física")["id"]
        s.asignar_materias(self.conn, self.ana, [self.mate, otra])
        s.eliminar_registro(self.conn, "materias", otra)
        self.assertEqual(len(s.materias_por_profesor(self.conn)[self.ana]), 1)

    def test_no_elimina_materia_con_clases(self):
        self.clase(self.ana, self.g6a, self.s101)
        with self.assertRaises(s.Conflicto):
            s.eliminar_registro(self.conn, "materias", self.mate)

    def test_validaciones(self):
        with self.assertRaises(s.ErrorDeNegocio):
            s.crear_franja(self.conn, 3, "10:00", "09:00")
        with self.assertRaises(s.NoEncontrado):
            s.crear_clase(self.conn, 99, self.ana, self.g6a, self.s101, 0, self.f1)
        with self.assertRaises(s.ErrorDeNegocio):
            s.crear_clase(self.conn, self.mate, self.ana, self.g6a, self.s101, 7, self.f1)


if __name__ == "__main__":
    unittest.main()

class PruebasGenerador(unittest.TestCase):
    def setUp(self):
        self.conn = conectar(":memory:")
        iniciar_db(self.conn)
        self.p1 = s.crear_catalogo(self.conn, "profesores", "Docente 1")["id"]
        self.p2 = s.crear_catalogo(self.conn, "profesores", "Docente 2")["id"]
        self.m1 = s.crear_catalogo(self.conn, "materias", "Matemáticas")["id"]
        self.m2 = s.crear_catalogo(self.conn, "materias", "Inglés")["id"]
        self.g1 = s.crear_catalogo(self.conn, "grupos", "6A")["id"]
        self.g2 = s.crear_catalogo(self.conn, "grupos", "7A")["id"]
        self.s1 = s.crear_catalogo(self.conn, "salones", "101")["id"]
        self.s2 = s.crear_catalogo(self.conn, "salones", "102")["id"]
        s.crear_franja(self.conn, 1, "07:00", "08:00")
        s.crear_franja(self.conn, 2, "08:00", "09:00")
        s.asignar_materias(self.conn, self.p1, [self.m1])
        s.asignar_materias(self.conn, self.p2, [self.m2])

    def tearDown(self):
        self.conn.close()

    def test_un_aula_principal_no_puede_pertenecer_a_dos_grupos(self):
        s.asignar_salon_grupo(self.conn, self.g1, self.s1)
        with self.assertRaisesRegex(s.ErrorDeNegocio, "ya está asignada al grupo 6A"):
            s.asignar_salon_grupo(self.conn, self.g2, self.s1)
        grupos, _ = s.asignaciones_aulas(self.conn)
        asignacion_7a = next(g for g in grupos if g["grupo_id"] == self.g2)
        self.assertIsNone(asignacion_7a["salon_id"])

    def test_generador_distribuye_cargas_sin_choques(self):
        s.asignar_salon_grupo(self.conn, self.g1, self.s1)
        s.asignar_salon_grupo(self.conn, self.g2, self.s2)
        for grupo in (self.g1, self.g2):
            s.guardar_carga(self.conn, grupo, self.m1, 2)
            s.guardar_carga(self.conn, grupo, self.m2, 1)
        total = s.generar_horario(self.conn)
        self.assertEqual(total, 6)
        self.assertEqual(len(s.listar_clases(self.conn)), 6)
        self.assertEqual(s.estadisticas(self.conn)["pendientes"], 0)

    def test_generador_respeta_aula_especializada(self):
        laboratorio = s.crear_aula(self.conn, "Aula de Sistemas", "especial")["id"]
        s.asignar_salon_grupo(self.conn, self.g1, self.s1)
        s.asignar_salon_grupo(self.conn, self.g2, self.s2)
        s.asignar_materias_salon(self.conn, laboratorio, [self.m2])
        s.guardar_carga(self.conn, self.g1, self.m1, 1)
        s.guardar_carga(self.conn, self.g1, self.m2, 1)
        s.asignar_materias(self.conn, self.p1, [self.m1, self.m2])
        s.generar_horario(self.conn)
        clases_ingles = s.listar_clases(self.conn, grupo_id=self.g1)
        self.assertEqual(next(c["salon_id"] for c in clases_ingles if c["materia_id"] == self.m2), laboratorio)

    def test_aula_especial_puede_tener_varias_materias(self):
        laboratorio = s.crear_aula(self.conn, "Laboratorio", "especial")["id"]
        s.asignar_materias_salon(self.conn, laboratorio, [self.m1, self.m2])
        asignadas = {r[0] for r in self.conn.execute(
            "SELECT materia_id FROM salon_materias WHERE salon_id=?", (laboratorio,))}
        self.assertEqual(asignadas, {self.m1, self.m2})

    def test_aula_principal_no_se_puede_asociar_a_materias_especiales(self):
        with self.assertRaises(s.ErrorDeNegocio):
            s.asignar_materias_salon(self.conn, self.s1, [self.m1])

    def test_grupo_no_puede_recibir_aula_especial_como_principal(self):
        laboratorio = s.crear_aula(self.conn, "Laboratorio", "especial")["id"]
        with self.assertRaises(s.ErrorDeNegocio):
            s.asignar_salon_grupo(self.conn, self.g1, laboratorio)

    def test_generador_no_borra_horario_si_falta_configuracion(self):
        s.crear_clase(self.conn, self.m1, self.p1, self.g1, self.s1, 0, 1)
        with self.assertRaises(s.ErrorDeNegocio):
            s.generar_horario(self.conn)
        self.assertEqual(len(s.listar_clases(self.conn)), 1)
