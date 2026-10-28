# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    shop_stock_qty = fields.Float(
        string="Stock en tienda",
        digits="Product Unit",
        copy=False,
        help="Stock que la tienda indicó tener del artículo al pedirlo desde la "
        "PDA. Dato informativo: no afecta a cantidades ni importes.",
    )
    shop_stock_reported = fields.Boolean(
        string="Stock informado",
        copy=False,
        readonly=True,
        help="Distingue un stock en tienda de 0 de uno no indicado.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Las altas desde el cliente web envían siempre el valor por
            # defecto (0), así que solo cuenta como informado si es distinto
            # de 0 o si se indica expresamente (la API de la PDA lo hace).
            if "shop_stock_reported" not in vals and vals.get("shop_stock_qty"):
                vals["shop_stock_reported"] = True
        return super().create(vals_list)

    def write(self, vals):
        if "shop_stock_qty" in vals and "shop_stock_reported" not in vals:
            vals = dict(vals, shop_stock_reported=True)
        return super().write(vals)
