# Horario escolar

Aplicación web para que un colegio organice su horario de clases, vea profesores
libres y sepa quién está en clase ahora. Tiene inicio de sesión con dos roles:

- **Rector (administrador)**: crea profesores (y sus accesos), materias, grupos,
  salones y franjas; arma y modifica el horario; consulta todo.
- **Profesor**: ve su propio horario, quién está en clase ahora y los profesores libres.

## Instalación (una sola vez)

```
pip install flask
```

## Ejecutar

```
python run.py
```

Abre http://127.0.0.1:5000 en el navegador. La primera vez te pedirá crear la
cuenta del rector. Después, desde el menú: crea franjas, materias, grupos, salones
y profesores, y configura la intensidad horaria en **Intensidad horaria**, asigna las materias a los profesores y genera el horario desde el panel del rector. También puedes editar clases manualmente desde la vista de horarios.

## Generación automática

1. Registra profesores, materias, grupos, salones y franjas.
2. En **Profesores**, asigna a cada docente las materias que puede dictar.
3. Abre **Intensidad horaria** y registra las horas semanales de cada materia para cada grupo.
4. Desde el panel del rector, pulsa **Generar horario automáticamente**.

El generador busca una distribución sin cruces de profesor, grupo o salón. Al confirmar, reemplaza el horario semanal que estuviera guardado. Si no encuentra una solución, conserva el horario anterior y muestra qué debes revisar.

## Cargar los profesores de una vez

```
python cargar_profesores.py
```

Crea los profesores de la lista, sus materias y sus accesos (usuario: primer nombre
y apellido, por ejemplo `carlos.gomez`; contraseña inicial `123456`). Edita la lista
`PROFESORES` dentro del archivo para cambiar los datos. Se puede ejecutar varias veces.

## Pruebas

```
python -m unittest -v
```

## Estructura

- `run.py`: arranca la app
- `app/web.py`: páginas y permisos por rol
- `app/servicio.py`: reglas del horario (choques, profesores libres, quién está ahora)
- `app/usuarios.py`: cuentas y contraseñas (se guardan cifradas)
- `app/db.py`: base de datos SQLite (`horarios.db`, se crea sola)
- `app/templates/`, `app/static/`: páginas, estilos y búsqueda de horarios
- `cargas_horarias`: intensidad semanal configurada por grupo y materia

## Asignación de aulas y disponibilidad

En el menú **Asignación de aulas** el rector puede:
- Crear aulas nuevas (por ejemplo, Aula de Sistemas o Laboratorio).
- Asignar un aula principal a cada grupo.
- Asociar materias con aulas especializadas. Si una materia tiene un aula especializada, el generador automático la programa allí; las demás materias utilizan el aula principal del grupo.

Antes de generar el horario automático, asigna un aula principal a cada grupo que tenga carga académica. La página **Profesores libres** ahora muestra una cuadrícula semanal: por defecto indica cuántos profesores están libres en cada día y franja, y permite seleccionar un docente para ver sus clases y espacios libres.

Los indicadores del panel administrativo son enlaces a sus respectivos módulos de detalle.


## Clasificación y asignación de aulas

En **Aulas y asignaciones** se crean los espacios indicando su tipo:

- **Aula principal:** se asigna a un único grupo y se utiliza para las materias sin aula especializada.
- **Aula especial:** se asocia con una o varias materias; puede ser utilizada por distintos grupos en diferentes franjas. Una materia puede tener más de un aula especial disponible y el generador seleccionará una que no esté ocupada.

Al iniciar la aplicación, la base de datos se actualiza automáticamente. Las aulas antiguas se consideran principales, excepto aquellas que ya tenían materias asociadas, que se migran como especiales.


## Módulos nuevos

- **Portal docente:** cada profesor puede iniciar sesión con su usuario y consultar su horario. Desde "Imprimir horario" puede imprimirlo o guardarlo como PDF. Los accesos se crean desde el catálogo Profesores, configurando usuario y contraseña.
- **Carga laboral:** Gestión docente muestra el total de clases programadas por profesor y las materias que tiene asignadas.
- **Ausencias y reemplazos:** el rector registra ausencias por fecha y asigna sustitutos a las clases afectadas; se valida que el sustituto no tenga otra clase simultánea.
- **Historial y versiones:** se registran movimientos de clases y se pueden guardar/restaurar versiones del horario.
- **Copias de seguridad:** crea una copia SQLite en la carpeta `respaldos` junto a la base de datos.
- **Editor visual:** en la vista de horario del rector, arrastra una clase a otra celda; el servidor valida choques antes de guardar.

La primera vez que se ejecute, las nuevas tablas se crean automáticamente sin borrar los registros existentes. Haz una copia de `horarios.db` antes de actualizar.

### Uso de los módulos nuevos

1. Entra como rector y abre **Gestión docente** desde la barra superior.
2. **Carga laboral:** compara las clases programadas por profesor. El conteo corresponde a periodos semanales en el horario vigente.
3. **Portal docente:** en el menú **Profesores**, crea un usuario y una contraseña para cada docente. El profesor inicia sesión con esas credenciales; solo puede consultar su horario y sus reemplazos asignados.
4. **Ausencias:** registra profesor, fecha y motivo. Para asignar sustituto, selecciona una clase de ese docente que corresponda al día de la ausencia. El sustituto debe tener asignada la materia y estar libre en esa franja.
5. **Editor visual:** abre un horario específico (grupo, profesor o salón), arrastra una clase y suéltala en otra celda. La operación se rechaza si produce cruces.
6. **Historial:** guarda una versión antes de hacer cambios importantes. Restaurar una versión sustituye el horario semanal actual, no los catálogos.
7. **Copia completa:** crea un archivo SQLite dentro de `respaldos/`, junto a `horarios.db`. Conserva esa carpeta al mover el proyecto.

El horario imprimible se abre desde **Imprimir horario** en el portal docente. En el cuadro de impresión del navegador elige **Guardar como PDF**.

## Interfaz renovada

- **Menú lateral** agrupado (Horario, Planificación, Personal y grupos, Sistema) que marca la página actual y
  se vuelve un menú desplegable en el celular.
- **Inicio** del rector con tarjetas de resumen y una lista de pasos antes de generar el horario; el profesor
  ve sus clases de hoy y los reemplazos que debe cubrir.
- **Horario** con tarjetas de colores por materia, botones rápidos por grupo y botón **Imprimir** para
  cualquier grupo, profesor o salón (también incluye los descansos).
- **Salones** se gestiona ahora solo en **Aulas** (donde también se puede cambiar el tipo de un aula);
  la carga de cada profesor aparece en **Profesores**; **Gestión docente** pasó a **Respaldos e historial**.
- Orden natural en todas las listas (6A, 7A… 10A, 11A).

## Novedades de esta versión

- **Franjas de descanso**: en **Franjas** cada franja puede ser *Clase* o *Descanso*. Los descansos salen
  como una fila amarilla en los horarios, no admiten clases y el generador los respeta. El orden de las
  franjas ahora es automático (por hora de inicio) y no se permiten franjas que se crucen.
- **Ausencias y reemplazos** (menú **Ausencias**): se registra una ausencia de uno o varios días y, para
  cada clase afectada, la app ofrece solo profesores libres (con ★ los que dictan esa materia). Se pueden
  quitar reemplazos y eliminar ausencias. El **Inicio** muestra quién cubre y quién falta sin reemplazo, y
  los profesores ausentes no aparecen como libres.
- **Mi cuenta**: el rector y los profesores pueden cambiar su propia contraseña.
- **Generador**: corregido el error al regenerar con un horario ya guardado, y ahora reparte las horas de una
  materia en días distintos cuando es posible.
- Al regenerar o restaurar el horario se eliminan los reemplazos ya asignados (las clases cambian).


## Plataforma para varias instituciones y propietario técnico

Esta versión agrega una consola central. Cada institución registrada tiene su propia base de datos SQLite, por lo que sus profesores, grupos, materias, horarios y cuentas quedan separados. La instalación anterior se conserva automáticamente como **Institución actual** (código `institucion-actual`); no se borran sus datos.

### Primera configuración

1. Ejecuta `python run.py`.
2. Abre `http://127.0.0.1:5000/ingresar`. Si todavía no existe el propietario, usa el enlace **Primera vez: crear la cuenta del propietario** y crea la cuenta (contraseña de mínimo 10 caracteres).
3. En **Ingreso de usuarios** elige el tipo de usuario: **Propietario**, **Rector** o **Profesor**. Rector y Profesor piden además el **código del colegio**. Las direcciones antiguas (`/login`, `/colegio`, `/propietario/login`) llevan a esta pantalla.
4. Desde la consola del propietario registra instituciones (con su primer rector). Cada institución se ve con su profesores, grupos y último ingreso (hora UTC).
5. **Rectores:** el propietario puede ver los rectores de cada colegio, restablecerles la contraseña (las contraseñas no se pueden ver) y crear otro rector.
6. **Entrar como soporte:** muestra una franja visible y la etiqueta "Propietario · soporte". La entrada, la salida y las acciones quedan en **Auditoría de soporte**.
7. **Copia de seguridad:** cada institución tiene su botón; las copias se guardan en `respaldos_plataforma/`.

### Personalización por colegio

Cada colegio muestra su nombre en el menú, la pestaña y los horarios impresos, y da la bienvenida en el Inicio. En **Personalizar colegio** (rector) se puede elegir un color y subir un logo (PNG, JPG, WEBP o GIF, máximo 300 KB).

### Aviso de claves iniciales

El Inicio del rector lista a los profesores que todavía no han cambiado la clave que se les asignó (se marca al crear o restablecer el acceso, y se quita cuando el profesor la cambia en **Mi cuenta**).

### Archivos de datos nuevos

- `plataforma.db`: cuentas del propietario, registro de instituciones y auditoría de soporte.
- `instituciones/<codigo>.db`: base de datos independiente de cada nueva institución.
- `horarios.db`: base de datos de la instalación original, conservada como `institucion-actual`.

Haz copias de seguridad de **todos** estos archivos y de la carpeta `instituciones`. No compartas `secret.key` ni las contraseñas. Para un servidor público, configura HTTPS, limita el acceso administrativo y utiliza una clave secreta de sesión privada y estable.
