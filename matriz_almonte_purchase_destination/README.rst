=====================================================
Matriz Almonte - Almacén destino en pedidos de compra
=====================================================

Añade el campo **Almacén destino** a los pedidos de compra.

* La mercancía se recibe en el almacén de «Entregar a» (normalmente central).
* Al validar cada recepción se crea automáticamente un traspaso interno desde
  la ubicación de recepción al stock del almacén destino, con las cantidades
  realmente recibidas (las recepciones parciales generan un traspaso cada una).
* El traspaso queda confirmado y reservado, enlazado al pedido (botón
  «Traspasos a destino») y a la recepción de origen.
* No se crea traspaso si no hay almacén destino o si coincide con el de
  recepción.

Limitación: pensado para recepciones en un paso.
