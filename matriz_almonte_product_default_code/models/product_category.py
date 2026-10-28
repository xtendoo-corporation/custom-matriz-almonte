import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CATEGORY_REFERENCE_PATTERN = re.compile(r"^[0-9]{2,3}$")


class ProductCategory(models.Model):
    _inherit = "product.category"

    product_reference_code = fields.Char(
        string="Referencia",
        help="Código numérico de 2 o 3 dígitos con el que empiezan las "
        "referencias de los productos nuevos de esta categoría. Puede "
        "repetirse en varias categorías: en ese caso comparten numeración.",
    )

    @api.constrains("product_reference_code")
    def _check_product_reference_code(self):
        for category in self.filtered("product_reference_code"):
            if not CATEGORY_REFERENCE_PATTERN.match(category.product_reference_code):
                raise ValidationError(_(
                    "La referencia de la categoría «%(category)s» debe tener 2 o 3 "
                    "dígitos numéricos (por ejemplo 01 o 123).",
                    category=category.display_name,
                ))
