# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import logging

from markupsafe import Markup

from odoo import models

_logger = logging.getLogger(__name__)

# La Brother QL-700 imprime a 300 dpi. Cada módulo (barra más estrecha) del
# Code128 se dibuja con un número ENTERO de puntos de impresora: con una
# imagen PNG reescalada las barras salían dentadas y el lector fallaba.
_MM_PER_DOT = 25.4 / 300
# Ancho máximo (mm) del código: la etiqueta mide 50 mm y la QL-700 deja
# ~1,5 mm de margen no imprimible por lado.
_BARCODE_MAX_WIDTH_MM = 46.0
_BARCODE_HEIGHT_MM = 8.0


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

    def _format_price(self, amount):
        """Formatea ``amount`` con 2 decimales y el símbolo "€" fijo.

        Se usa un símbolo de moneda FIJO (en lugar de derivarlo de
        ``product.currency_id`` con ``format_amount``, cuyo símbolo puede no
        mostrarse según la moneda/idioma configurados) porque en la etiqueta
        impresa siempre debe aparecer "€".

        Se devuelve como ``Markup`` con la entidad HTML numérica del euro
        (``&#8364;``) en lugar del carácter Unicode literal: el documento
        HTML final que procesa wkhtmltopdf pierde la declaración de charset
        UTF-8 (ver comentario junto al <meta charset> de la plantilla), lo
        que hacía que el carácter "€" se mostrase corrupto (p.ej. "‚"). La
        entidad numérica se interpreta correctamente pase lo que pase con
        la codificación del documento. Al ir envuelta en ``Markup`` y
        pintarse con ``t-out`` en la plantilla, QWeb no la escapa.
        """
        formatted = f"{amount:.2f}".replace(".", ",")
        return Markup("%s &#8364;") % formatted

    def _get_barcode_svg(self, value):
        """SVG vectorial (``Markup``) del código de barras Code128 de ``value``.

        Cada módulo ocupa 3 puntos de impresora (2 si el código es largo y
        no cabe en la etiqueta), de modo que las barras salen nítidas. Si
        ``value`` está vacío o no se puede codificar devuelve ``False``.
        """
        if not value:
            return False
        try:
            from reportlab.graphics.barcode.code128 import Code128

            barcode = Code128(value, barWidth=1, quiet=0)
            barcode.validate()
            barcode.encode()
            barcode.decompose()
            # reportlab: mayúsculas A-D = barra de 1-4 módulos, minúsculas
            # a-d = espacio de 1-4 módulos.
            rects = []
            position = 0
            for char in barcode.decomposed:
                if char.isupper():
                    width = ord(char) - 64
                    rects.append(
                        '<rect x="%d" y="0" width="%d" height="1"/>'
                        % (position, width)
                    )
                else:
                    width = ord(char) - 96
                position += width
        except Exception:  # noqa: BLE001 - un código inválido no debe romper el PDF
            _logger.warning(
                "No se ha podido generar el código de barras para %r", value
            )
            return False

        dots = 3 if position * 3 * _MM_PER_DOT <= _BARCODE_MAX_WIDTH_MM else 2
        width_mm = min(position * dots * _MM_PER_DOT, _BARCODE_MAX_WIDTH_MM)
        return Markup(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d 1" '
            'preserveAspectRatio="none" width="%.3fmm" height="%.1fmm" '
            'shape-rendering="crispEdges">%s</svg>'
        ) % (position, width_mm, _BARCODE_HEIGHT_MM, Markup("".join(rects)))

    def _get_label_data(self, product):
        """Diccionario con los datos a mostrar en una etiqueta de ``product``."""
        price_with_tax = self._get_price_with_tax(product)
        return {
            "product": product,
            "default_code": product.default_code or "",
            "name": product.name,
            "price": self._format_price(price_with_tax),
            "barcode_svg": self._get_barcode_svg(product.default_code),
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


class MatrizAlmonteProductLabelReportPlata(
    models.AbstractModel
):
    """Parser del informe de etiquetas "Plata".

    Reutiliza íntegramente la lógica del parser estándar (mismos datos de
    producto: referencia, nombre, precio con IVA y código de barras). La
    única diferencia está en la PLANTILLA asociada, que solo pinta la
    referencia y el precio.
    """

    _name = "report.matriz_almonte_product_label.product_label_report_plata"
    _inherit = "report.matriz_almonte_product_label.product_label_report"
    _description = "Parser del informe de etiquetas de producto Plata"


