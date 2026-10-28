# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Endpoints para que las PDA de tienda pidan mercancía a un proveedor."""

import json
import logging
import math

from markupsafe import Markup, escape

from odoo import Command, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.matriz_almonte_pda_sale_import.controllers.pda_pos_order_controller import (  # noqa: E501
    MatrizAlmontePdaPosOrderController,
)

_logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 512 * 1024
_MAX_LINES = 500
_NO_SELLER_WARNING = (
    "El artículo no tiene asignado este proveedor; central revisará el precio."
)
_NO_SHOP_STOCK_WARNING = "No se indicó el stock actual de la tienda."


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


class MatrizAlmontePdaStoreDemandController(http.Controller):
    """Pedidos de compra en borrador creados desde la PDA de una tienda.

    La tienda (almacén destino) sale del punto de venta del token; la
    mercancía se recibe en el almacén por defecto de compras (central) y
    ``matriz_almonte_purchase_destination`` genera el traspaso a la tienda.
    """

    _ROUTE = "/api/matriz_almonte/pda/store_demand"
    _SUPPLIERS_ROUTE = "/api/matriz_almonte/pda/suppliers"

    @http.route(
        _SUPPLIERS_ROUTE,
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_list_suppliers(self, **kwargs):
        token_rec, error_response = MatrizAlmontePdaPosOrderController()._authenticate_token()
        if error_response:
            return error_response
        company = token_rec.tienda_id.company_id or request.env.company
        suppliers = request.env["res.partner"].search(
            self._supplier_domain(company), order="name, id"
        )
        return _json_response({
            "success": True,
            "code": "OK",
            "count": len(suppliers),
            "proveedores": [
                {
                    "id": supplier.id,
                    "nombre": supplier.name or "",
                    "ref": supplier.ref or "",
                    "nif": supplier.vat or "",
                }
                for supplier in suppliers
            ],
        })

    @http.route(
        _ROUTE,
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_create_store_demand(self, **kwargs):
        token_rec, error_response = MatrizAlmontePdaPosOrderController()._authenticate_token()
        if error_response:
            return error_response

        payload, error_response = self._read_payload()
        if error_response:
            return error_response

        uuid = str(payload.get("uuid") or "").strip()
        if not uuid:
            return _error("MISSING_UUID", "El campo 'uuid' es obligatorio.")

        pos_config = token_rec.tienda_id
        warehouse = pos_config.picking_type_id.warehouse_id
        if not warehouse:
            return _error(
                "TOKEN_WITHOUT_WAREHOUSE",
                "El punto de venta del token no tiene almacén asociado.",
            )
        company = pos_config.company_id
        order_model = request.env["purchase.order"].with_company(company)
        existing = order_model.search(
            [("pda_uuid", "=", uuid), ("company_id", "=", company.id)], limit=1
        )
        if existing:
            return self._ok(existing, duplicate=True)

        supplier, error_response = self._get_supplier(payload.get("id_proveedor"), company)
        if error_response:
            return error_response

        try:
            lines = self._prepare_lines(payload.get("lineas"))
            note = str(payload.get("nota") or "").strip()
            with request.env.cr.savepoint():
                order = order_model.create({
                    "pda_uuid": uuid,
                    "partner_id": supplier.id,
                    "company_id": company.id,
                    "dest_warehouse_id": warehouse.id,
                    "origin": "PDA %s" % (pos_config.name or token_rec.name),
                    "note": note or False,
                    "order_line": [
                        Command.create({
                            "product_id": product.id,
                            "product_qty": qty,
                            "shop_stock_qty": shop_stock or 0.0,
                            "shop_stock_reported": shop_stock is not None,
                        })
                        for product, qty, shop_stock in lines
                    ],
                })
                seller_warnings = self._seller_warnings(order)
                self._post_creation_message(order, token_rec, note, seller_warnings)
                warnings = seller_warnings + self._shop_stock_warnings(order)
        except (UserError, ValidationError) as exc:
            _logger.warning("PDA pedido %s rechazado: %s", uuid, exc)
            return _error("INVALID_DEMAND", str(exc.args[0] if exc.args else exc))
        except Exception:
            _logger.exception("PDA pedido %s: error inesperado", uuid)
            return _error("SERVER_ERROR", "Error interno al crear el pedido.", 500)

        return self._ok(order, warnings=warnings)

    @staticmethod
    def _supplier_domain(company):
        seller_partners = request.env["product.supplierinfo"].search(
            [("company_id", "in", [False, company.id])]
        ).partner_id
        return [
            ("company_id", "in", [False, company.id]),
            "|",
            ("supplier_rank", ">", 0),
            ("id", "in", seller_partners.ids),
        ]

    @classmethod
    def _get_supplier(cls, supplier_id, company):
        if supplier_id in (None, "", False):
            return None, _error(
                "MISSING_SUPPLIER", "El campo 'id_proveedor' es obligatorio."
            )
        try:
            supplier_id = int(supplier_id)
        except (TypeError, ValueError):
            return None, _error("INVALID_SUPPLIER", "El proveedor no es válido.")
        supplier = request.env["res.partner"].search(
            [("id", "=", supplier_id)] + cls._supplier_domain(company), limit=1
        )
        if not supplier:
            return None, _error(
                "INVALID_SUPPLIER",
                "El proveedor %s no existe o no está dado de alta como proveedor."
                % supplier_id,
            )
        return supplier, None

    @staticmethod
    def _seller_warnings(order):
        partners = order.partner_id | order.partner_id.commercial_partner_id
        warnings = []
        for line in order.order_line:
            product = line.product_id
            sellers = product._prepare_sellers().filtered(
                lambda seller: seller.partner_id in partners
                and (not seller.product_id or seller.product_id == product)
            )
            if not sellers:
                warnings.append({
                    "id_articulo": product.id,
                    "nombre": product.display_name,
                    "mensaje": _NO_SELLER_WARNING,
                })
        return warnings

    @staticmethod
    def _shop_stock_warnings(order):
        return [
            {
                "id_articulo": line.product_id.id,
                "nombre": line.product_id.display_name,
                "mensaje": _NO_SHOP_STOCK_WARNING,
            }
            for line in order.order_line
            if not line.shop_stock_reported
        ]

    @staticmethod
    def _format_qty(qty):
        return ("%.3f" % qty).rstrip("0").rstrip(".")

    @classmethod
    def _post_creation_message(cls, order, token_rec, note, seller_warnings):
        body = Markup("Pedido creado desde la PDA <b>%s</b> para la tienda <b>%s</b>.") % (
            token_rec.name,
            order.dest_warehouse_id.name,
        )
        if note:
            body += Markup("<br/>Nota de la tienda: %s") % note
        line_items = Markup("").join(
            Markup("<li>%s: pide %s, en tienda %s</li>") % (
                line.product_id.display_name,
                cls._format_qty(line.product_qty),
                cls._format_qty(line.shop_stock_qty)
                if line.shop_stock_reported else "sin indicar",
            )
            for line in order.order_line
        )
        body += Markup("<ul>%s</ul>") % line_items
        if seller_warnings:
            items = Markup("").join(
                Markup("<li>%s</li>") % escape(warning["nombre"])
                for warning in seller_warnings
            )
            body += Markup(
                "<br/>Artículos sin este proveedor asignado (revisar precio):<ul>%s</ul>"
            ) % items
        order.message_post(body=body)

    @staticmethod
    def _ok(order, duplicate=False, warnings=None):
        return _json_response({
            "success": True,
            "code": "DUPLICATE" if duplicate else "PURCHASE_CREATED",
            "duplicate": duplicate,
            "message": (
                "El pedido ya estaba registrado en Odoo: %s" % order.name
                if duplicate
                else "Pedido enviado a central: %s" % order.name
            ),
            "order_id": order.id,
            "order_name": order.name,
            "state": order.state,
            "avisos": warnings or [],
        })

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
    def _parse_shop_stock(line, product_id):
        """Devuelve el stock de tienda de la línea, o ``None`` si no se indicó.

        ``0`` es un valor válido y distinto de "no indicado"; ``null`` se trata
        como no indicado.
        """
        value = line.get("stock_tienda")
        if value is None:
            return None
        error = ValidationError(
            "Stock de tienda no válido para el artículo %s: debe ser un número "
            "mayor o igual que cero." % product_id
        )
        if isinstance(value, bool):
            raise error
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise error
        if not math.isfinite(value) or value < 0:
            raise error
        return value

    @classmethod
    def _prepare_lines(cls, lines):
        """Agrupa por producto y valida; devuelve ``[(product, qty, stock), ...]``.

        Las unidades de un mismo artículo se suman. El stock de tienda NO se
        suma (es el mismo stock físico informado varias veces): se usa el de la
        última línea que lo indique, o ``None`` si ninguna lo hace.
        """
        if not isinstance(lines, list) or not lines:
            raise ValidationError("El pedido no tiene líneas.")
        if len(lines) > _MAX_LINES:
            raise ValidationError("Demasiadas líneas (máximo %s)." % _MAX_LINES)
        quantities = {}
        shop_stocks = {}
        for line in lines:
            try:
                product_id = int(line.get("id_articulo"))
                qty = float(line.get("unidades"))
            except (AttributeError, TypeError, ValueError):
                raise ValidationError("Línea con artículo o unidades no válidos.")
            if not math.isfinite(qty) or qty <= 0:
                raise ValidationError("Las unidades deben ser mayores que cero.")
            quantities[product_id] = quantities.get(product_id, 0.0) + qty
            shop_stock = cls._parse_shop_stock(line, product_id)
            if shop_stock is not None:
                shop_stocks[product_id] = shop_stock
        products = request.env["product.product"].browse(list(quantities)).exists()
        missing = set(quantities) - set(products.ids)
        if missing:
            raise ValidationError(
                "Artículos inexistentes en Odoo: %s." % sorted(missing)
            )
        not_purchasable = products.filtered(lambda product: not product.purchase_ok)
        if not_purchasable:
            raise ValidationError(
                "Artículos no comprables: %s."
                % ", ".join(not_purchasable.mapped("display_name"))
            )
        return [
            (products.browse(product_id), qty, shop_stocks.get(product_id))
            for product_id, qty in quantities.items()
        ]
