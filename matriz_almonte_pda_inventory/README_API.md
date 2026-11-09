# API PDA - Inventario de tienda

Autenticación: `Authorization: Bearer <token>` o `X-API-Token: <token>`, y
`Content-Type: application/json`.

El token decide dónde y quién:

- **Almacén**: el del punto de venta del token (`tienda_id`). El recuento se
  registra en su ubicación de existencias (`<ALMACÉN>/Stock`).
- **Usuario**: el «Usuario de Ventas» del token queda asignado al recuento.

## POST /api/matriz_almonte/pda/inventory

```json
{"uuid": "3f0c9c1e-...", "fecha_hora": "09/11/2026 10:35:12",
 "lineas": [{"id_articulo": "REF001", "unidades": 12},
            {"id_articulo": "REF002", "unidades": 3.5}]}
```

- `id_articulo`: **Referencia** del producto (`default_code`), como texto para
  conservar los ceros (`"010001"` y `"10001"` son artículos distintos). Se
  acepta también `ref` con el mismo significado.
- `unidades`: stock real contado, decimal ≥ 0. Si una referencia se repite
  (contada en varios sitios), las unidades se suman.
- `uuid` y `fecha_hora` son opcionales; `uuid` solo se usa en los logs.

### Qué hace

Por cada artículo enviado escribe su **Cantidad contada** en el Inventario
físico estándar de Odoo (*Inventario > Operaciones > Inventario físico*,
filtro «Para aplicar»), igual que si se tecleara en esa pantalla. **No mueve
stock**: alguien en Odoo revisa la diferencia y pulsa «Aplicar», y entonces el
stock del artículo pasa a ser lo contado.

- Cada envío recibe un número (`INV/00045`) y los recuentos quedan marcados
  con ese número y la PDA que lo envió (columna «Recuento PDA», filtro «Desde
  PDA»). Al aplicarlo, el movimiento de ajuste lleva esa referencia
  (`INV/00045 (PDA <nombre del token>)`) en *Inventario > Informes > Historial
  de movimientos*. Si alguien cambia a mano la cantidad contada, la marca se
  quita y el ajuste queda como uno normal de Odoo.
- Cada envío deja en el log de Odoo las referencias y unidades recibidas.
- Los artículos que no se envían **no se tocan**.
- Reenviar el mismo recuento deja la misma cantidad contada (no se suma).
  Un recuento nuevo de un artículo sustituye al pendiente anterior.
- No se inventarían (se devuelven en `avisos` y el resto se registra igual):
  referencias inexistentes, referencias asignadas a varios artículos, artículos
  sin control de stock y artículos con lotes/números de serie.
- Una línea mal formada (sin `id_articulo`, unidades negativas o no numéricas)
  rechaza el envío entero (`INVALID_INVENTORY`) sin registrar nada.

Respuesta:

```json
{"success": true, "order_id": 45, "order_name": "INV/00045",
 "message": "Inventario registrado",
 "code": "INVENTORY_COUNTED", "almacen": "Olga", "ubicacion": "OLGA/Stock", "articulos_contados": 2,
 "avisos": [{"id_articulo": "REF009", "nombre": "", "mensaje": "Artículo inexistente en Odoo."}]}
```

`order_id` es el número del recuento y `order_name` su referencia; el resto de
campos son informativos.

Errores (`success: false`): `MISSING_TOKEN`/`INVALID_TOKEN` (401),
`EMPTY_BODY`, `INVALID_JSON`, `TOKEN_WITHOUT_WAREHOUSE`, `INVALID_INVENTORY`
(400), `PAYLOAD_TOO_LARGE` (413, máx. 2 MB), `SERVER_ERROR` (500).
