# API PDA - Inventario de tienda

Autenticación: `Authorization: Bearer <token>` o `X-API-Token: <token>`, y
`Content-Type: application/json`.

El token decide dónde y quién:

- **Almacén**: el del punto de venta del token (`tienda_id`). El recuento se
  registra en su ubicación de existencias (`<ALMACÉN>/Stock`).
- **Usuario**: el «Usuario de Ventas» del token queda asignado al recuento.

## POST /api/matriz_almonte/pda/inventory

```json
{"uuid": "3f0c9c1e-...",
 "fecha_hora_inicio": "09/11/2026 09:12:40",
 "fecha_hora_envio": "09/11/2026 10:35:12",
 "comentario": "Recuento de la sección de frutería, falta la cámara 2",
 "lineas": [{"id_articulo": "REF001", "unidades": 12},
            {"id_articulo": "REF002", "unidades": 3.5}]}
```

- `id_articulo`: **Referencia** del producto (`default_code`), como texto para
  conservar los ceros (`"010001"` y `"10001"` son artículos distintos). Se
  acepta también `ref` con el mismo significado.
- `unidades`: unidades contadas en este envío, decimal ≥ 0. Se suman a lo que
  ya hubiera contado del artículo en el inventario abierto.
- `fecha_hora_inicio` / `fecha_hora_envio`: inicio y envío del recuento, en
  hora local de la tienda (`dd/mm/aaaa hh:mm:ss`; también `dd/mm/aaaa hh:mm`
  o ISO). Se toma la zona horaria del «Usuario de Ventas» del token (si no
  tiene, Europe/Madrid). El inicio no puede ser posterior al envío. Se acepta
  `fecha_hora` (formato anterior) como fecha de envío.
- `comentario`: texto libre del recuento (máx. 2000 caracteres).
- Todos los campos salvo `lineas` son opcionales; `uuid` solo se usa en los
  logs. Una fecha o un comentario no válidos rechazan el envío
  (`INVALID_INVENTORY`).

### Qué hace

Suma las unidades recibidas a la **Cantidad contada** del Inventario físico
estándar de Odoo (*Inventario > Operaciones > Ajustes > Inventario físico*,
filtro «Para aplicar»). **No mueve stock**: alguien en Odoo revisa la
diferencia y pulsa «Aplicar», y entonces el stock pasa a ser lo contado.

- **Número de inventario por ubicación.** Si en `<ALMACÉN>/Stock` no hay
  ninguna línea de la PDA pendiente de aplicar, el envío abre un inventario
  nuevo (`INVENTORY/00045`). Si ya hay un inventario abierto, el envío se suma a él,
  venga de la PDA que venga. El inventario sigue abierto mientras le quede
  alguna línea pendiente; una vez aplicadas todas, el siguiente envío abre otro
  número. Las líneas llevan el número en la columna «Inventario PDA» (filtro
  «Desde PDA», y se puede buscar por número) y, al aplicarlas, el movimiento de
  ajuste lleva ese número como referencia en *Inventario > Informes >
  Historial de movimientos*.
- **Se suma, no se sustituye.** Si el artículo ya tiene una cantidad contada
  pendiente (de otro envío, o tecleada a mano), las unidades recibidas se le
  suman. Si no, pasan a ser su cantidad contada. Corregir a mano una línea la
  mantiene en el inventario; el siguiente envío suma a lo corregido.
- **Registro de cada línea recibida** (*Inventario > Operaciones > Ajustes >
  Envíos de inventario PDA*): una fila por artículo y envío, con el número de
  inventario, la PDA, el uuid, las unidades recibidas, la cantidad contada tras
  sumarlas, inicio, envío y comentario. Si la línea tiene 10 porque llegaron
  dos envíos de 5, aquí hay dos filas. En el Inventario físico, la columna
  «Envíos PDA» dice cuántos envíos tiene cada línea y el botón del historial
  los abre. El registro se conserva después de aplicar.
- **Envíos repetidos.** Si la misma PDA vuelve a mandar el mismo `uuid` con las
  mismas líneas (un reintento), no se suma otra vez: se responde con el número
  del inventario y `"duplicado": true`. Un `uuid` repetido con otras líneas se
  trata como un envío nuevo. Sin `uuid` no se puede reconocer el reintento.
- **Al recibir no se pone nada a 0**: el inventario solo lleva lo contado.
  Si ninguna referencia del envío se puede inventariar, se rechaza
  (`INVALID_INVENTORY`): un inventario vacío pondría a 0 la tienda entera.
- **Inventario completo al aplicarlo.** Cuando se aplica la última línea
  pendiente de un inventario (en una vez o en varias), se abre su **puesta a
  0** (*Inventario > Operaciones > Ajustes > Puestas a 0 tras inventario
  PDA*) y la tarea programada «PDA: poner a 0 lo no inventariado» la ejecuta
  enseguida, por tandas de 300 líneas para no bloquear Odoo: todo el stock de
  la ubicación del inventario (`<ALMACÉN>/Stock` y sus sububicaciones) que no
  sea la línea contada de un artículo del inventario se pone a 0 y se aplica
  directamente. Eso incluye el stock en sububicaciones o paquetes de los
  artículos contados, para que el total de la tienda sea lo contado. Los
  movimientos llevan el número del inventario como referencia y el comentario
  «No estaba en el inventario: puesto a 0.». No se tocan: artículos con
  lotes/números de serie, líneas con un recuento pendiente (otro inventario o
  un recuento a mano en curso) ni otros almacenes. Si algo falla, la puesta a
  0 queda «Error» con el motivo y un responsable de inventario puede
  «Reintentar». Si la última línea pendiente se borra en lugar de aplicarse,
  no hay puesta a 0.
- **Inicio, envío y comentario** se guardan en cada línea del registro. En la
  línea del Inventario físico quedan el inicio más antiguo, el envío más
  reciente y los comentarios de todos sus envíos, y pasan al movimiento de
  ajuste al aplicarlo (mismas columnas en el Historial de movimientos; las
  fechas vienen ocultas, se activan en el selector de columnas).
- Cada envío deja en el log de Odoo las referencias y unidades recibidas.
- No se inventarían (se devuelven en `avisos` y el resto se registra igual):
  referencias inexistentes, referencias asignadas a varios artículos, artículos
  sin control de stock y artículos con lotes/números de serie.
- Una línea mal formada (sin `id_articulo`, unidades negativas o no numéricas)
  rechaza el envío entero (`INVALID_INVENTORY`) sin registrar nada. Si una
  referencia se repite en el mismo envío, sus unidades se suman.

Respuesta:

```json
{"success": true, "order_id": 45, "order_name": "INVENTORY/00045",
 "message": "Inventario registrado",
 "code": "INVENTORY_COUNTED", "almacen": "Olga", "ubicacion": "OLGA/Stock", "articulos_contados": 2,
 "duplicado": false,
 "avisos": [{"id_articulo": "REF009", "nombre": "", "mensaje": "Artículo inexistente en Odoo."}]}
```

`order_id` es el número del inventario y `order_name` su referencia; todos los
envíos que se suman al mismo inventario reciben el mismo. El resto de campos
son informativos.

Errores (`success: false`): `MISSING_TOKEN`/`INVALID_TOKEN` (401),
`EMPTY_BODY`, `INVALID_JSON`, `TOKEN_WITHOUT_WAREHOUSE`, `INVALID_INVENTORY`
(400), `PAYLOAD_TOO_LARGE` (413, máx. 2 MB), `SERVER_ERROR` (500).
