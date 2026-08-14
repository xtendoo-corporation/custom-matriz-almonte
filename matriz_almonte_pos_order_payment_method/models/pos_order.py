# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Campo calculado con la(s) forma(s) de pago usada(s) en el pedido POS."""

from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    payment_method_names = fields.Char(
        string="Forma(s) de pago",
        compute="_compute_payment_method_names",
        help=(
            "Nombre de la forma de pago (o formas de pago, separadas por "
            "comas) utilizada(s) para pagar el pedido."
        ),
    )

    @api.depends("payment_ids.payment_method_id.name")
    def _compute_payment_method_names(self):
        for order in self:
            # Nombres únicos preservando el orden de aparición de los pagos.
            names = list(
                dict.fromkeys(
                    order.payment_ids.mapped("payment_method_id.name")
                )
            )
            order.payment_method_names = ", ".join(name for name in names if name)

