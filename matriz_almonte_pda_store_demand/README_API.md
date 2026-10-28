# API PDA - Pedidos a proveedor desde tienda

Autenticación: `Authorization: Bearer <token>` o `X-API-Token: <token>`.
La tienda destino es el almacén del punto de venta del token.

## GET /api/matriz_almonte/pda/suppliers

Proveedores de la compañía (`supplier_rank > 0` o con tarifas de proveedor).

```json
{"success": true, "code": "OK", "count": 1,
 "proveedores": [{"id": 57, "nombre": "Cerería", "ref": "P001", "nif": "B12345678"}]}
```

## POST /api/matriz_almonte/pda/store_demand

```json
{"uuid": "6f1c...", "id_proveedor": 57, "nota": "opcional",
 "lineas": [{"id_articulo": 1234, "unidades": 10}]}
```

Crea una solicitud de presupuesto (borrador) al proveedor con la tienda como
almacén destino. Las líneas del mismo artículo se suman. Reenviar el mismo
`uuid` devuelve el pedido existente (`code: DUPLICATE`).

```json
{"success": true, "code": "PURCHASE_CREATED", "duplicate": false,
 "message": "Pedido enviado a central: P00123", "order_id": 123,
 "order_name": "P00123", "state": "draft",
 "avisos": [{"id_articulo": 5678, "nombre": "Medalla", "mensaje": "..."}]}
```

`avisos` lista los artículos sin ese proveedor asignado (precio 0; el pedido se
crea igualmente).

Errores (`success: false`): `MISSING_TOKEN`/`INVALID_TOKEN` (401),
`EMPTY_BODY`, `INVALID_JSON`, `MISSING_UUID`, `MISSING_SUPPLIER`,
`INVALID_SUPPLIER`, `TOKEN_WITHOUT_WAREHOUSE`, `INVALID_DEMAND` (400),
`PAYLOAD_TOO_LARGE` (413), `SERVER_ERROR` (500).
