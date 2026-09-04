===============================================
Matriz Almonte - Diario de Ventas POS (Excel)
===============================================

Este módulo genera, a partir de los pedidos del Punto de Venta, un
**Diario de Ventas** descargable en Excel (``.xlsx``) con las
siguientes columnas:

* Número de la venta
* Fecha de la venta
* Forma de pago
* % IVA
* Neto
* Importe de IVA
* Importe total del ticket

El informe permite filtrar por:

* Rango de fechas (**Desde** / **Hasta**).
* Uno o varios puntos de venta concretos, o bien **todos** los puntos
  de venta a la vez (marcando la opción "Todos los puntos de venta").

Desglose en varias líneas
==========================

Cada ticket genera **una fila por cada combinación de forma de pago y
tipo de IVA** que contenga:

* Si el ticket se pagó con una única forma de pago y todas sus líneas
  llevan el mismo IVA, se genera **una sola fila**.
* Si el ticket se pagó con **varias formas de pago** (pago mixto,
  p. ej. parte en efectivo y parte con tarjeta), se genera **una fila
  por cada forma de pago**.
* Si el ticket combina **varios tipos de IVA** (p. ej. 21 % y 10 % en
  el mismo ticket), se genera **una fila por cada tipo de IVA**.
* Si se dan ambos casos a la vez, se generan tantas filas como
  combinaciones existan (formas de pago × tipos de IVA), repartiendo el
  Neto y la Cuota de IVA de cada tipo impositivo de forma proporcional
  al peso de cada forma de pago sobre el total del ticket. Así, la
  suma de "Neto", "Importe de IVA" e "Importe total del ticket" de
  todas las filas de un mismo ticket siempre cuadra exactamente con
  sus importes reales (no se duplica ni se pierde importe alguno al
  sumar el diario completo).

Uso
===

1. Ir a **Punto de Venta > Reporting > Diario de Ventas POS (Excel)**.
2. Indicar el rango de fechas (por defecto, del primer día del mes
   actual hasta hoy).
3. Dejar marcado "Todos los puntos de venta" o, si se desea filtrar,
   desmarcarlo y seleccionar uno o varios puntos de venta concretos.
4. Pulsar **Exportar a Excel** para descargar el archivo ``.xlsx``.

Origen de cada columna
========================

Número de la venta
    ``pos_reference`` del pedido (o ``name`` si no hubiera referencia).
Fecha de la venta
    ``date_order``, convertida a la zona horaria del usuario.
Forma de pago
    Nombre del método de pago (``pos.payment.payment_method_id.name``)
    de cada pago registrado en el ticket.
% IVA
    Porcentaje de IVA de cada grupo de líneas (0 si están exentas).
Neto
    Base imponible (``price_subtotal``) de las líneas de ese tipo de
    IVA, prorrateada según el peso de la forma de pago sobre el total
    del ticket.
Importe de IVA
    Cuota de IVA (``price_subtotal_incl - price_subtotal``) de las
    líneas de ese tipo de IVA, prorrateada igual que el Neto.
Importe total del ticket
    Neto + Importe de IVA de esa misma fila (no es el total del
    ticket completo repetido en cada línea, sino la parte que
    corresponde a esa combinación de forma de pago y tipo de IVA).

Notas técnicas
===============

* Se excluyen del informe los pedidos en estado "Cancelado".
* Las fechas del filtro se interpretan en la zona horaria del usuario y
  se convierten a UTC para compararlas con ``pos.order.date_order``.
* Si un ticket no tiene ningún pago registrado (caso anómalo), se
  genera igualmente una fila con la forma de pago vacía y el importe
  total del pedido, para no perder la venta en el diario.
* Este módulo depende del motor `report_xlsx
  <https://github.com/OCA/reporting-engine>`_ de la OCA.

