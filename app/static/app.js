document.addEventListener('DOMContentLoaded', () => {
  // Los avisos se pueden cerrar con un clic; los de éxito se ocultan solos.
  document.querySelectorAll('.mensajes .msg').forEach(m => {
    m.addEventListener('click', () => m.remove());
    if (!m.classList.contains('error')) setTimeout(() => m.remove(), 7000);
  });
  // En el celular, al tocar un enlace del menú se cierra.
  document.querySelectorAll('.lateral a').forEach(a =>
    a.addEventListener('click', () => document.body.classList.remove('menu-abierto')));
});
