# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import base64
import logging

from odoo import models
from odoo.tools import format_amount

_logger = logging.getLogger(__name__)


class MatrizAlmonteProductLabelReport(models.AbstractModel):
    _name = "report.matriz_almonte_product_label.product_label_report"
    _description = "Parser del informe de etiquetas de producto Brother QL-700"

    def _get_price_with_tax(self, product):
        """Precio de venta del producto (list_price) con impuestos incluidos."""
        price = product.list_price
        company = product.company_id or self.env.company
        taxes = product.taxes_id.filtered(
            lambda tax: not tax.company_id or tax.company_id == company
        )
        if taxes:
            res = taxes.compute_all(
                price,
                currency=product.currency_id,
                quantity=1.0,
                product=product,
            )
            price = res["total_included"]
        return price

    def _get_barcode_image(self, value):
        """Imagen (data URI PNG en base64) del código de barras Code128 de ``value``.

        Se genera directamente en Python (en lugar de referenciar la URL del
        controlador ``/report/barcode/``) para que wkhtmltopdf no tenga que
        realizar una petición HTTP adicional al propio servidor mientras
        genera el PDF, lo que resulta más robusto en entornos Docker/proxy.
        """
        if not value:
            return False
        try:
            barcode = self.env["ir.actions.report"].barcode(
                "Code128",
                value,
                width=600,
                height=150,
                humanreadable=0,
                quiet=1,
            )
        except Exception:  # noqa: BLE001 - un código inválido no debe romper el PDF
            _logger.warning(
                "No se ha podido generar el código de barras para %r", value
            )
            return False
        return "data:image/png;base64,%s" % base64.b64encode(barcode).decode()

    def _get_label_data(self, product):
        """Diccionario con los datos a mostrar en una etiqueta de ``product``."""
        price_with_tax = self._get_price_with_tax(product)
        return {
            "product": product,
            "default_code": product.default_code or "",
            "name": product.name,
            "price": format_amount(self.env, price_with_tax, product.currency_id),
            "barcode_image": self._get_barcode_image(product.default_code),
        }

    def _get_report_values(self, docids, data=None):
        data = data or {}
        quantity = data.get("custom_quantity") or 1
        product_ids = data.get("product_ids") or docids
        products = self.env["product.product"].browse(product_ids).exists()
        labels = []
        for product in products:
            label = self._get_label_data(product)
            labels += [label] * max(int(quantity), 1)
        return {
            "doc_ids": docids,
            "doc_model": "product.product",
            "docs": products,
            "labels": labels,
        }


