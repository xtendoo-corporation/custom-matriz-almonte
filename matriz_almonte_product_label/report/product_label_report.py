# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import base64
import logging
from io import BytesIO

from odoo import models
from odoo.tools import format_amount

_logger = logging.getLogger(__name__)

# Resolución interna de renderizado de wkhtmltopdf (usada por Odoo cuando el
# report.paperformat no fuerza un --zoom de compensación, ver dpi=96 en
# report_paperformat.xml). 1mm equivale a estos "px CSS".
_PX_PER_MM = 96.0 / 25.4
# Altura física deseada del código de barras impreso en la etiqueta.
_BARCODE_HEIGHT_MM = 9.7


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
        """Datos de la imagen del código de barras Code128 de ``value``.

        Devuelve un diccionario con ``src`` (data URI PNG en base64),
        ``width`` y ``height`` (en "px CSS", a incluir como atributos HTML
        del <img>). Se generan explícitamente estos dos últimos porque
        wkhtmltopdf, con imágenes ``data:`` incrustadas, no siempre respeta
        el tamaño indicado únicamente por CSS: el chunk de resolución física
        (pHYs) que reportlab escribe en el PNG hace que la imagen se dibuje
        a su tamaño intrínseco en píxeles interpretados como puntos,
        produciendo una imagen enorme (p.ej. 600x150 "pt" en vez de mm).
        Fijar ``width``/``height`` como atributos HTML (además de eliminar
        esos metadatos con Pillow) es la solución robusta y ampliamente
        documentada para este problema conocido de wkhtmltopdf.
        """
        if not value:
            return False
        try:
            raw = self.env["ir.actions.report"].barcode(
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

        try:
            from PIL import Image

            image = Image.open(BytesIO(raw))
            src_width, src_height = image.size
            # Volver a guardar sin metadatos de resolución física (pHYs),
            # para que wkhtmltopdf no calcule el tamaño de la imagen a
            # partir de un DPI embebido en lugar de usar el HTML/CSS.
            image.info.pop("dpi", None)
            buffer = BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            raw = buffer.getvalue()
        except Exception:  # noqa: BLE001
            _logger.warning(
                "No se ha podido normalizar el PNG del código de barras de %r",
                value,
            )
            src_width, src_height = 600, 150

        height_px = max(1, round(_BARCODE_HEIGHT_MM * _PX_PER_MM))
        width_px = max(1, round(height_px * src_width / src_height))
        return {
            "src": "data:image/png;base64,%s" % base64.b64encode(raw).decode(),
            "width": width_px,
            "height": height_px,
        }

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


