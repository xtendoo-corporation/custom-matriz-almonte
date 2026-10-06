# API PDA — `POST /api/matriz_almonte/pda/pos/order`

## Respuesta (campos nuevos, todos aditivos)

| Campo | Significado |
|---|---|
| `duplicate` | `true` si la `external_reference` ya existía (HTTP 200, mismo `order_id`/`amount_total`). |
| `amount_total` | Total real del pedido en Odoo. |
| `amount_mismatch` | `true` si el total de Odoo difiere (en céntimos) del `amount_total` enviado. No se rechaza ni se altera el pedido; queda marcado (`pda_amount_mismatch`) y en el log (`AMOUNT_MISMATCH`). |
| `printed` / `print_queued` / `print_error` | Resultado de la impresión. Un fallo de impresión nunca falla la venta. |
| `invoice_pending` | `true` si la factura está pendiente o fallida (solo con facturación diferida). |
| `timings_ms` | Milisegundos por fase: `total`, `auth`, `validate`, `lookup`, `create`, `commit`, `invoice`, `print`. |

Códigos de error (`code`): `VALIDATION_ERROR` (payload inválido), `UNKNOWN_PRODUCT`,
`BAD_PAYMENT`, `SESSION_NOT_OPEN` (409), `MISSING_TOKEN`/`INVALID_TOKEN` (401),
`SERVER_ERROR` (500, reintentable). Los códigos históricos (`CREATED`, `DUPLICATE`,
`VALIDATION_ERROR`) se mantienen.

## Idempotencia

`pos.order.pda_external_reference` es único a nivel de BD. Una referencia repetida
(secuencial o simultánea) devuelve el pedido existente con `duplicate: true`. Si dos
peticiones compiten, la que pierde el INSERT captura el `IntegrityError` y devuelve el
pedido ganador.

## Fórmula de importes

* `price_unit` llega **con IVA incluido**; `discount` es un % sobre todas las líneas.
* Por línea: `unit = price_unit × (1 − discount/100)`; el motor de impuestos de Odoo
  (`compute_all` con `force_price_include`) obtiene base e IVA hacia atrás.
  `line.price_unit` se guarda en la convención del impuesto (con/sin IVA incluido).
* El total del pedido es el que calcula Odoo (`_compute_prices`, con su redondeo
  por línea o global). Se compara con el `amount_total` de la PDA.
* Cobro automático: se paga el `amount_total` calculado por Odoo.

## Facturación diferida (ajuste, **desactivado por defecto**)

Ajustes → Punto de Venta → «Facturación diferida PDA». Con él activo:
1. Se crea el pedido y se paga → **commit**.
2. Si hay impresión: se factura en el acto (el ticket ES la factura simplificada) y se imprime.
   Si no, se encola el cron `PDA: facturar pedidos pendientes` (cada 5 min, más disparo inmediato).
3. Si la factura falla: el pedido sigue pagado, `pda_invoice_state = error`, se guarda el
   motivo y se reintenta hasta 10 veces.

## Conciliación

* `GET|POST /pda/pos/summary` `{"date": "YYYY-MM-DD"}` → `count`, `amount_total`,
  `by_payment` (cash/card), `references` (+`offset`, `limit`, `has_more`). Solo pedidos PDA
  del TPV del token; el día se interpreta en la zona horaria del usuario de ventas.
* `POST /pda/pos/status` `{"references": [...]}` → `found` / `missing` (máx. 500).
