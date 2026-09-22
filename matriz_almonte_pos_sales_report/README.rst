===============================================
Matriz Almonte - Diario de Ventas POS (Excel)
===============================================

Este módulo genera, a partir de los pedidos del Punto de Venta, un
**resumen de caja totalizado por punto de venta** descargable en
Excel (``.xlsx``). Por cada combinación de **fecha** y **punto de
venta** se genera un bloque con una matriz que cruza el **tipo de
IVA** (filas) con la **forma de pago** (columnas), mostrando también
los totales por fila y por columna, al estilo de un cierre de caja
diario::

    Resumen del 20/08/2026              (A/000001 / A/000098)
    PUNTO DE VENTA: DESPACHO 1
    IVA        Efectivo    Tarjeta     Total
    0%         13,50 €     4,50 €      18,00 €
    21%        18,50 €     0,00 €      18,50 €
    TOTAL      32,00 €     4,50 €      36,50 €

El importe de cada celda es el importe **realmente cobrado** (base +
cuota de IVA) para esa combinación de tipo de IVA y forma de pago; no
es la base imponible sola, ya que el objetivo de este informe es
cuadrar el dinero recibido en caja, no servir de libro de IVA.

El informe permite filtrar por:

* Rango de fechas (**Desde** / **Hasta**).
* Uno o varios puntos de venta concretos, o bien **todos** los puntos
  de venta a la vez (marcando la opción "Todos los puntos de venta").

Agrupación en bloques
======================

* Se genera **un bloque por cada día** con ventas dentro del rango de
  fechas seleccionado, y **un bloque por cada punto de venta** que
  haya tenido ventas ese día (si se seleccionan varios puntos de
  venta, o "todos", puede haber varios bloques para el mismo día, uno
  debajo de otro).
* Las **columnas de forma de pago** se calculan dinámicamente a partir
  de las formas de pago realmente usadas en el rango de fechas
  filtrado (no están limitadas a "Efectivo" y "Tarjeta"; si se usa
  Bizum, transferencia, etc., aparecerán como columnas adicionales).
  Así, todos los bloques del informe muestran las mismas columnas,
  aunque en algún bloque alguna forma de pago no se haya usado (se
  mostrará "0,00 €" en ese caso).
* Si existen varias formas de pago prioritarias (Efectivo, Tarjeta),
  se muestran siempre en ese orden y en primer lugar; el resto de
  formas de pago se añaden después, por orden alfabético.
* Las **filas de tipo de IVA** se calculan por bloque: solo se listan
  los tipos de IVA que realmente aparecen en las ventas de ese
  bloque concreto, ordenados de menor a mayor porcentaje.

Reparto de tickets con varias formas de pago y/o varios IVA
=============================================================

* Si un ticket se pagó con una única forma de pago y todas sus líneas
  llevan el mismo IVA, su importe cae íntegro en una sola celda.
* Si el ticket se pagó con **varias formas de pago** (pago mixto,
  p. ej. parte en efectivo y parte con tarjeta), su importe se reparte
  entre las columnas correspondientes.
* Si el ticket combina **varios tipos de IVA** (p. ej. 21 % y 0 % en
  el mismo ticket), su importe se reparte entre las filas
  correspondientes.
* Si se dan ambos casos a la vez, el importe de cada tipo de IVA se
  reparte entre las formas de pago de forma proporcional al peso de
  cada una sobre el total del ticket. Así, la suma de todas las
  celdas de un bloque siempre cuadra exactamente con la suma de los
  importes totales de los pedidos incluidos en él (fila y columna
  "Total"/"TOTAL").

Uso
===

1. Ir a **Punto de Venta > Reporting > Diario de Ventas POS (Excel)**.
2. Indicar el rango de fechas (por defecto, del primer día del mes
   actual hasta hoy).
3. Dejar marcado "Todos los puntos de venta" o, si se desea filtrar,
   desmarcarlo y seleccionar uno o varios puntos de venta concretos.
4. Pulsar **Exportar a Excel** para descargar el archivo ``.xlsx``.

Origen de cada dato
======================

Fecha del bloque ("Resumen del ...")
    ``date_order`` de los pedidos, convertida a la zona horaria del
    usuario y agrupada por día natural.
Rango de ventas entre paréntesis
    ``pos_reference`` (o ``name`` si no hubiera referencia) del primer
    y del último pedido del bloque, ordenados por ``date_order``.
Punto de venta
    Nombre de ``pos.config`` (``order.config_id.name``).
Columnas de forma de pago
    Nombre del método de pago (``pos.payment.payment_method_id.name``)
    de cada pago registrado en los tickets del rango filtrado.
Filas de % IVA
    Porcentaje de IVA de cada grupo de líneas (0 % si están exentas).
Importe de cada celda
    Base imponible (``price_subtotal``) + cuota de IVA
    (``price_subtotal_incl - price_subtotal``) de las líneas de ese
    tipo de IVA, prorrateada según el peso de esa forma de pago sobre
    el total del ticket.
Columna y fila "Total" / "TOTAL"
    Suma de los importes de la fila o columna correspondiente.

Notas técnicas
===============

* Se excluyen del informe los pedidos en estado "Cancelado".
* Las fechas del filtro se interpretan en la zona horaria del usuario y
  se convierten a UTC para compararlas con ``pos.order.date_order``.
* Si un ticket no tiene ningún pago registrado (caso anómalo), se
  contabiliza en una columna "Sin forma de pago" con el importe total
  del pedido, para no perder la venta del resumen.
* Este módulo depende del motor `report_xlsx
  <https://github.com/OCA/reporting-engine>`_ de la OCA.

