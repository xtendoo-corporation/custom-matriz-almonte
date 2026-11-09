# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Endpoint para recibir el inventario (recuento físico) de una tienda."""

import json
import logging
import math

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.matriz_almonte_pda_sale_import.controllers.pda_pos_order_controller import (  # noqa: E501
    MatrizAlmontePdaPosOrderController,
)

_logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_LINES = 20000

NOT_FOUND = "Artículo inexistente en Odoo."
AMBIGUOUS = "La referencia está asignada a varios artículos en Odoo."
NOT_STORABLE = "Artículo sin control de stock: no se inventaría."
TRACKED = "Artículo con lotes/números de serie: contarlo desde Odoo."


def _json_response(data, status=200):
    return request.make_response(
        json.dumps(data, ensure_ascii=False, default=str),
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
        status=status,
    )


def _error(code, message, status=400):
    return _json_response(
        {"success": False, "code": code, "message": message}, status=status
    )


class MatrizAlmontePdaInventoryController(http.Controller):
    """Recuento físico de la tienda enviado desde la PDA.

    El almacén sale del punto de venta del token y el recuento se asigna a su
    «Usuario de Ventas». Las cantidades quedan como «Cantidad contada» en el
    Inventario físico estándar de Odoo, pendientes de aplicar.
    """

    _ROUTE = "/api/matriz_almonte/pda/inventory"

    @http.route(
        _ROUTE,
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_inventory(self, **kwargs):
        token_rec, error_response = (
            MatrizAlmontePdaPosOrderController()._authenticate_token()
        )
        if error_response:
            return error_response

        payload, error_response = self._read_payload()
        if error_response:
            return error_response

        warehouse = token_rec.tienda_id.picking_type_id.warehouse_id
        if not warehouse:
            return _error(
                "TOKEN_WITHOUT_WAREHOUSE",
                "El punto de venta del token no tiene almacén asociado.",
            )
        company = warehouse.company_id
        reference = str(payload.get("uuid") or "").strip()

        try:
            quantities = self._parse_lines(payload.get("lineas"))
            counts, warnings = self._resolve_products(quantities, company)
            with request.env.cr.savepoint():
                quants = (
                    request.env["stock.quant"]
                    .with_company(company)
                    ._pda_set_counted_quantities(
                        warehouse.lot_stock_id,
                        counts,
                        token_rec.sale_user_id or request.env.user,
                    )
                )
        except (UserError, ValidationError) as exc:
            _logger.warning("PDA inventario %s rechazado: %s", reference, exc)
            return _error("INVALID_INVENTORY", str(exc.args[0] if exc.args else exc))
        except Exception:
            _logger.exception("PDA inventario %s: error inesperado", reference)
            return _error(
                "SERVER_ERROR", "Error interno al registrar el inventario.", 500
            )

        _logger.info(
            "PDA inventario %s (%s): %s artículos contados en %s, %s avisos",
            reference,
            token_rec.name,
            len(quants),
            warehouse.name,
            len(warnings),
        )
        return _json_response(
            {
                "success": True,
                "code": "INVENTORY_COUNTED",
                "message": (
                    "Recuento registrado en %s: %s artículos pendientes de aplicar "
                    "en el Inventario físico." % (warehouse.name, len(quants))
                ),
                "almacen": warehouse.name,
                "ubicacion": warehouse.lot_stock_id.complete_name,
                "articulos_contados": len(quants),
                "avisos": warnings,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_payload():
        raw = request.httprequest.get_data(as_text=False)
        if not raw:
            return None, _error("EMPTY_BODY", "El cuerpo de la petición está vacío.")
        if len(raw) > _MAX_PAYLOAD_BYTES:
            return None, _error(
                "PAYLOAD_TOO_LARGE", "El payload supera el tamaño máximo.", 413
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return None, _error("INVALID_JSON", "JSON mal formado: %s" % exc)
        if not isinstance(payload, dict):
            return None, _error("INVALID_JSON", "El payload debe ser un objeto JSON.")
        return payload, None

    @staticmethod
    def _parse_ref(line):
        """Referencia (``default_code``) de la línea, siempre como texto.

        Se acepta ``id_articulo`` (formato de la PDA) o ``ref``. Un número JSON
        se trata como la misma referencia en texto; nunca se convierte a número
        para no perder ceros.
        """
        ref = line.get("id_articulo", line.get("ref"))
        if isinstance(ref, int) and not isinstance(ref, bool):
            ref = str(ref)
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError("Línea sin 'id_articulo' válido.")
        return ref.strip()

    @staticmethod
    def _parse_qty(line, ref):
        value = line.get("unidades")
        error = ValidationError(
            "Unidades no válidas para el artículo %s: debe ser un número mayor o "
            "igual que cero." % ref
        )
        if value is None or isinstance(value, bool):
            raise error
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise error from None
        if not math.isfinite(value) or value < 0:
            raise error
        return value

    @classmethod
    def _parse_lines(cls, lines):
        """Valida las líneas y devuelve ``{ref: unidades}``.

        Un artículo repetido (p. ej. contado en dos estanterías) suma sus
        unidades. Una línea mal formada rechaza el envío entero.
        """
        if not isinstance(lines, list) or not lines:
            raise ValidationError("El inventario no tiene líneas.")
        if len(lines) > _MAX_LINES:
            raise ValidationError("Demasiadas líneas (máximo %s)." % _MAX_LINES)
        quantities = {}
        for line in lines:
            if not isinstance(line, dict):
                raise ValidationError("Línea de inventario no válida.")
            ref = cls._parse_ref(line)
            quantities[ref] = quantities.get(ref, 0.0) + cls._parse_qty(line, ref)
        return quantities

    @staticmethod
    def _resolve_products(quantities, company):
        """Devuelve ``({product: unidades}, avisos)``.

        Las referencias que no se pueden inventariar no bloquean el resto del
        recuento: se devuelven como avisos.
        """
        products = request.env["product.product"].search(
            [
                ("default_code", "in", list(quantities)),
                ("company_id", "in", [False, company.id]),
            ]
        )
        by_ref = {}
        for product in products:
            by_ref.setdefault(product.default_code, []).append(product)
        counts = {}
        warnings = []

        def warn(ref, message, product=None):
            warnings.append(
                {
                    "id_articulo": ref,
                    "nombre": product.display_name if product else "",
                    "mensaje": message,
                }
            )

        for ref, qty in quantities.items():
            matches = by_ref.get(ref, [])
            if not matches:
                warn(ref, NOT_FOUND)
            elif len(matches) > 1:
                warn(ref, AMBIGUOUS)
            elif not matches[0].is_storable:
                warn(ref, NOT_STORABLE, matches[0])
            elif matches[0].tracking != "none":
                warn(ref, TRACKED, matches[0])
            else:
                counts[matches[0]] = qty
        return counts, warnings
