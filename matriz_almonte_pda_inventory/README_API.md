# API PDA - Inventario de tienda

Autenticación: `Authorization: Bearer <token>` o `X-API-Token: <token>`.

El token decide dónde y quién:

- **Almacén**: el del punto de venta del token (`tienda_id`). Se inventaría su
  ubicación de existencias (`<ALMACÉN>/Stock`) con todas sus sububicaciones.
- **Usuario**: el «Usuario de Ventas» del token queda como responsable del
  inventario y de los movimientos de ajuste.

## POST /api/matriz_almonte/pda/inventory

```json
{"uuid": "6f1c...", "nota": "opcional",
 "lineas": [{"ref": "010001", "unidades": 12},
            {"ref": "109040", "unidades": 0}]}
```

- `uuid`: obligatorio. Reenviar el mismo `uuid` no vuelve a aplicar nada y
  devuelve el inventario existente (`code: DUPLICATE`).
- `ref`: **Referencia** del producto (`default_code`), como texto para
  conservar los ceros (`"010001"` y `"10001"` son artículos distintos). Se
  acepta también `id_articulo` con el mismo significado.
- `unidades`: cantidad real contada, decimal ≥ 0. Si una referencia se repite
  (contada en varios sitios), las unidades se suman.

### Qué hace (inventario completo, se aplica al recibir)

- Artículo contado: su stock en el almacén pasa a ser lo contado (en la
  ubicación de existencias; el resto de sububicaciones de ese artículo en el
  almacén quedan a 0).
- Artículo **no contado** con stock en el almacén: **se pone a 0**.
- Artículo con lotes/números de serie contado: no se ajusta (la PDA no envía el
  lote) y se devuelve un aviso para revisarlo en Odoo.
- Artículo sin control de stock (servicio/consumible): no se ajusta, aviso.
- Si alguna `ref` no existe o está repetida en varios artículos de Odoo, se
  rechaza **todo** el inventario sin tocar el stock (`INVALID_INVENTORY`).

Queda registrado en *Inventario > Operaciones > Ajustes > Inventarios PDA*,
con stock anterior, contado y diferencia por artículo. Los movimientos de
ajuste llevan como referencia el número del inventario (`PDA-INV/AAAA/NNNNN`).

```json
{"success": true, "code": "INVENTORY_APPLIED", "duplicate": false,
 "message": "Inventario aplicado: PDA-INV/2026/00001",
 "inventory_id": 1, "inventory_name": "PDA-INV/2026/00001",
 "almacen": "Olga", "articulos_contados": 2, "articulos_ajustados": 15,
 "articulos_a_cero": 13,
 "avisos": [{"ref": "LOT-1", "id_articulo": 77, "nombre": "Pulsera",
             "mensaje": "Artículo con lotes/números de serie: revisar el inventario en Odoo."}]}
```

Errores (`success: false`): `MISSING_TOKEN`/`INVALID_TOKEN` (401),
`EMPTY_BODY`, `INVALID_JSON`, `MISSING_UUID`, `TOKEN_WITHOUT_WAREHOUSE`,
`INVALID_INVENTORY` (400), `PAYLOAD_TOO_LARGE` (413, máx. 2 MB),
`SERVER_ERROR` (500).
