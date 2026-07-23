# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Controlador HTTP para exponer productos a dispositivos PDA."""

import json
import logging

from odoo import http
from odoo.http import request

from .pda_sale_import_controller import MatrizAlmontePdaSaleImportController

_logger = logging.getLogger(__name__)


def _json_response(data, status=200):
    """Devuelve una respuesta JSON con cabeceras homogéneas."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return request.make_response(
        body,
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
        status=status,
    )


def _error(code, message, http_status=400):
    """Construye una respuesta de error homogénea."""
    return _json_response(
        {
            "success": False,
            "code": code,
            "message": message,
            "count": 0,
            "productos": [],
        },
        status=http_status,
    )


def _remote_ip():
    """Devuelve la IP del cliente de forma segura."""
    return (
        request.httprequest.headers.get("X-Forwarded-For", "")
        or request.httprequest.remote_addr
        or "unknown"
    )


class MatrizAlmontePdaProductCatalogController(http.Controller):
    """Endpoint de catálogo para sincronizar productos en la app Android."""

    _ROUTE = "/api/matriz_almonte/pda/products"

    @http.route(
        _ROUTE,
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_product_catalog(self, **kwargs):
        """Devuelve el catálogo base de productos para la PDA."""
        auth_header = request.httprequest.headers.get("Authorization", "")
        token_value = MatrizAlmontePdaSaleImportController._extract_bearer_token(
            auth_header
        )

        if not token_value:
            token_value = request.httprequest.headers.get("X-API-Token", "").strip()

        if not token_value:
            _logger.warning(
                "PDA product catalog: petición sin token desde %s",
                _remote_ip(),
            )
            return _error(
                "MISSING_TOKEN",
                "Se requiere autenticación. Incluye 'Authorization: Bearer <token>'.",
                http_status=401,
            )

        token_rec = (
            request.env["matriz.almonte.api.token"].sudo().authenticate(token_value)
        )
        if not token_rec:
            _logger.warning(
                "PDA product catalog: token inválido o inactivo desde %s",
                _remote_ip(),
            )
            return _error(
                "INVALID_TOKEN",
                "Token de autenticación inválido o inactivo.",
                http_status=401,
            )

        products = (
            request.env["product.product"]
            .sudo()
            .search(
                [("active", "=", True), ("sale_ok", "=", True)],
                order="default_code, id",
            )
        )

        return _json_response(
            {
                "success": True,
                "code": "PRODUCTS_OK",
                "message": "Productos obtenidos correctamente.",
                "count": len(products),
                "productos": [
                    {
                        "id": product.id,
                        "nombre": product.name,
                        "codigo_barras": product.barcode or "",
                        "referencia": product.default_code or "",
                        "precio_costo": product.standard_price,
                        "precio_venta": product.lst_price,
                        "porcentaje_iva": self._get_vat_percent(product),
                    }
                    for product in products
                ],
            },
            status=200,
        )

    @staticmethod
    def _get_vat_percent(product):
        """Devuelve el porcentaje del primer impuesto de venta del producto."""
        sale_taxes = product.taxes_id.filtered(lambda tax: tax.type_tax_use == "sale")
        if not sale_taxes:
            return 0.0
        sale_tax = sale_taxes.sorted(lambda tax: (tax.sequence, tax.id))[0]
        return float(sale_tax.amount)
