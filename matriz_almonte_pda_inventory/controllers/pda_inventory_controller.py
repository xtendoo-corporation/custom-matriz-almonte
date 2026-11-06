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


def _json_response(data, status=200):
    return request.make_response(
        json.dumps(data, ensure_ascii=False, default=str),
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
        status=status,
    )


def _error(code, message, status=400, **extra):
    payload = {"success": False, "code": code, "message": message}
    payload.update(extra)
    return _json_response(payload, status=status)


class MatrizAlmontePdaInventoryController(http.Controller):
    """Inventario completo del almacén de la tienda enviado desde la PDA.

    El almacén sale del punto de venta del token y el usuario responsable es
    el «Usuario de Ventas» del token. El recuento se aplica al recibirlo.
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

        uuid = str(payload.get("uuid") or "").strip()
        if not uuid:
            return _error("MISSING_UUID", "El campo 'uuid' es obligatorio.")

        warehouse = token_rec.tienda_id.picking_type_id.warehouse_id
        if not warehouse:
            return _error(
                "TOKEN_WITHOUT_WAREHOUSE",
                "El punto de venta del token no tiene almacén asociado.",
            )
        company = warehouse.company_id
        inventory_model = request.env["matriz.almonte.pda.inventory"].with_company(
            company
        )
        existing = inventory_model.search(
            [("pda_uuid", "=", uuid), ("company_id", "=", company.id)], limit=1
        )
        if existing:
            return self._ok(existing, duplicate=True)

        try:
            counts = self._prepare_counts(payload.get("lineas"), company)
            note = str(payload.get("nota") or "").strip()
            with request.env.cr.savepoint():
                inventory = inventory_model._apply_pda_count(
                    token_rec, warehouse, uuid, counts, note=note
                )
        except (UserError, ValidationError) as exc:
            _logger.warning("PDA inventario %s rechazado: %s", uuid, exc)
            return _error(
                "INVALID_INVENTORY", str(exc.args[0] if exc.args else exc)
            )
        except Exception:
            _logger.exception("PDA inventario %s: error inesperado", uuid)
            return _error("SERVER_ERROR", "Error interno al aplicar el inventario.", 500)

        _logger.info(
            "PDA inventario %s aplicado en %s (%s contados, %s ajustados)",
            inventory.name,
            warehouse.name,
            inventory.counted_count,
            inventory.adjusted_count,
        )
        return self._ok(inventory)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ok(inventory, duplicate=False):
        warnings = [
            {
                "ref": line.pda_ref or line.product_id.default_code or "",
                "id_articulo": line.product_id.id,
                "nombre": line.product_id.display_name,
                "mensaje": line.skip_reason,
            }
            for line in inventory.line_ids
            if line.state == "skipped"
        ]
        return _json_response(
            {
                "success": True,
                "code": "DUPLICATE" if duplicate else "INVENTORY_APPLIED",
                "duplicate": duplicate,
                "message": (
                    "El inventario ya estaba aplicado en Odoo: %s" % inventory.name
                    if duplicate
                    else "Inventario aplicado: %s" % inventory.name
                ),
                "inventory_id": inventory.id,
                "inventory_name": inventory.name,
                "almacen": inventory.warehouse_id.name,
                "articulos_contados": inventory.counted_count,
                "articulos_ajustados": inventory.adjusted_count,
                "articulos_a_cero": inventory.zeroed_count,
                "avisos": warnings,
            }
        )

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

        Se acepta ``ref`` o, por compatibilidad con el resto de la API,
        ``id_articulo``. Un número JSON se trata como la misma referencia en
        texto; nunca se convierte a número para no perder ceros.
        """
        ref = line.get("ref", line.get("id_articulo"))
        if isinstance(ref, int) and not isinstance(ref, bool):
            ref = str(ref)
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError("Línea sin 'ref' de artículo válida.")
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
    def _prepare_counts(cls, lines, company):
        """Valida las líneas y devuelve ``{product: (cantidad, ref)}``.

        Un artículo repetido (p. ej. contado en dos estanterías) suma sus
        unidades. Como el inventario es completo y pone a 0 lo no contado, una
        referencia inexistente o ambigua rechaza todo el inventario.
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

        products = request.env["product.product"].search(
            [
                ("default_code", "in", list(quantities)),
                ("company_id", "in", [False, company.id]),
            ]
        )
        products_by_ref = {}
        for product in products:
            products_by_ref.setdefault(product.default_code, []).append(product)
        missing = [ref for ref in quantities if ref not in products_by_ref]
        if missing:
            raise ValidationError(
                "Artículos inexistentes en Odoo: %s." % ", ".join(missing)
            )
        ambiguous = [ref for ref, prods in products_by_ref.items() if len(prods) > 1]
        if ambiguous:
            raise ValidationError(
                "Referencias asignadas a varios artículos en Odoo: %s."
                % ", ".join(sorted(ambiguous))
            )
        return {
            products_by_ref[ref][0]: (qty, ref) for ref, qty in quantities.items()
        }
