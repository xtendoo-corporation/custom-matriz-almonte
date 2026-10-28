# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    pda_uuid = fields.Char(
        string="UUID PDA",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="Identificador único generado por la PDA; evita duplicar el "
        "pedido si la PDA reenvía.",
    )

    _pda_uuid_company_unique = models.Constraint(
        "UNIQUE(company_id, pda_uuid)",
        "Ya existe un pedido de compra con ese UUID de PDA.",
    )
