from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    store_demand_line_ids = fields.One2many(
        "matriz.store.demand.line",
        "purchase_line_id",
        string="Líneas de pre-pedido",
        readonly=True,
        groups="matriz_almonte_store_demand.group_store_demand_manager",
    )


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    store_demand_ids = fields.Many2many(
        "matriz.store.demand",
        string="Pre-pedidos de tienda",
        compute="_compute_store_demand_ids",
        groups="matriz_almonte_store_demand.group_store_demand_manager",
    )
    store_demand_count = fields.Integer(
        compute="_compute_store_demand_ids",
        groups="matriz_almonte_store_demand.group_store_demand_manager",
    )

    @api.depends("order_line.store_demand_line_ids")
    def _compute_store_demand_ids(self):
        for order in self:
            order.store_demand_ids = order.order_line.store_demand_line_ids.demand_id
            order.store_demand_count = len(order.store_demand_ids)

    def action_view_store_demands(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "matriz_almonte_store_demand.action_store_demand"
        )
        action["domain"] = [("id", "in", self.store_demand_ids.ids)]
        action["context"] = {"create": False}
        return action
