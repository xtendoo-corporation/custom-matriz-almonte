# Matriz Almonte - Pre-pedidos de tienda

Las tiendas piden a central y central compra de forma agrupada.

## Flujo

1. **Tienda** (grupo *Pre-pedidos / Tienda*): crea un pre-pedido. La tienda se
   rellena con el almacén por defecto del usuario. Añade productos y pulsa
   **Enviar a central**. Cada usuario de tienda solo ve los pre-pedidos de su
   almacén.
2. **Central** (grupo *Pre-pedidos / Central*): en *Demanda pendiente* ve todas
   las líneas enviadas, agrupadas por proveedor, producto o tienda. Pulsa
   *Consolidar demanda* (o selecciona líneas y usa *Consolidar selección*).
3. El asistente agrupa la demanda por producto y proveedor y propone
   `a comprar = demanda − stock libre en central`. Las cantidades y el
   proveedor se pueden ajustar.
4. Al confirmar se crea:
   - un **pedido de compra (RFQ) por proveedor**, con recepción en el almacén
     central;
   - un **traspaso interno por tienda**, de central a la tienda, con la
     cantidad pedida.
5. Cuando llega la compra a central, los traspasos se reservan solos. Al
   validar cada traspaso, el pre-pedido pasa a **Servido**; si queda una
   entrega pendiente (backorder), espera a que se valide.

## Configuración

- Marca el almacén central con **Central de pre-pedidos**. Solo puede haber
  uno por compañía. Se hace en *Pre-pedidos > Configuración > Almacenes y
  tiendas*.
- Asigna a cada usuario de tienda su **almacén por defecto** (preferencias del
  usuario) y el grupo *Tienda*.
- Los productos necesitan **proveedor** (pestaña Compra) para proponerlo
  automáticamente. Si falta, se puede indicar en el asistente.
