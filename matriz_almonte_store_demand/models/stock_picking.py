from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self):
        result = super()._action_done()
        pickings = self
        while pickings.backorder_id - pickings:
            pickings |= pickings.backorder_id
        demand_lines = self.env["matriz.store.demand.line"].sudo().search([
            ("move_id.picking_id", "in", pickings.ids),
        ])
        demand_lines.demand_id._update_state_from_lines()
        return result
