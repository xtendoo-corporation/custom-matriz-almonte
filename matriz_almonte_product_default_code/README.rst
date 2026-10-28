=====================================================
Matriz Almonte - Referencia de producto por categoría
=====================================================

Genera automáticamente la referencia interna de los productos nuevos.

Funcionamiento
==============

* Cada categoría de producto tiene un campo **Referencia** con 2 o 3 dígitos
  numéricos (por ejemplo ``01``). Varias categorías pueden compartir código y,
  en ese caso, comparten numeración.
* Al crear un producto con la referencia vacía se asigna
  ``<referencia categoría><6 dígitos>``. El número es el más alto existente con
  ese mismo código y longitud, más uno, contando también los productos
  archivados. Ejemplo: tras ``01000002`` viene ``01000003``.
* Si el usuario escribe la referencia a mano, se respeta.
* Si la referencia viene vacía y la categoría no tiene código, la creación se
  bloquea con un aviso.
* No se permiten referencias duplicadas al crear o cambiar una referencia. Los
  duplicados antiguos se mantienen mientras no se modifique su referencia.
* Las referencias existentes no se modifican y cambiar la categoría no
  regenera la referencia.
* Los productos técnicos que cargan los módulos al instalarse no se numeran.
* Un bloqueo en PostgreSQL por código evita que dos creaciones simultáneas
  reciban el mismo número.

Configuración
=============

Inventario / Ventas > Configuración > Categorías de producto: rellenar la
Referencia de cada categoría que se vaya a usar al crear productos (el filtro
"Sin referencia" ayuda a localizarlas).
