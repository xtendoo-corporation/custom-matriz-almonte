# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    # Datos del recuento de la PDA del que sale el ajuste de inventario.
    pda_count_start = fields.Datetime(
        string="Inicio recuento PDA", readonly=True, copy=False
    )
    pda_count_end = fields.Datetime(
        string="Envío recuento PDA", readonly=True, copy=False
    )
    pda_count_comment = fields.Text(
        string="Comentario recuento PDA", readonly=True, copy=False
    )


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    pda_count_start = fields.Datetime(related="move_id.pda_count_start")
    pda_count_end = fields.Datetime(related="move_id.pda_count_end")
    pda_count_comment = fields.Text(related="move_id.pda_count_comment")
