from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    dest_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Almacén destino",
        check_company=True,
        tracking=True,
        index="btree_not_null",
        help="Tienda a la que va la mercancía. Se recibe en el almacén de "
        "«Entregar a» y, al validar cada recepción, se crea automáticamente "
        "el traspaso con lo recibido a este almacén.",
    )
    destination_picking_ids = fields.One2many(
        "stock.picking",
        "purchase_destination_id",
        string="Traspasos a destino",
        readonly=True,
    )
    destination_picking_count = fields.Integer(
        compute="_compute_destination_picking_count",
    )

    @api.depends("destination_picking_ids")
    def _compute_destination_picking_count(self):
        for order in self:
            order.destination_picking_count = len(order.destination_picking_ids)

    @api.constrains("dest_warehouse_id")
    def _check_dest_warehouse_not_removed(self):
        for order in self:
            if order.destination_picking_ids and not order.dest_warehouse_id:
                raise ValidationError(_(
                    "El pedido %s ya tiene traspasos a destino; no se puede quitar "
                    "el almacén destino.",
                    order.name,
                ))

    def _needs_destination_transfer(self):
        self.ensure_one()
        return bool(
            self.dest_warehouse_id
            and self.dest_warehouse_id != self.picking_type_id.warehouse_id
        )

    def action_view_destination_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        action["domain"] = [("id", "in", self.destination_picking_ids.ids)]
        action["context"] = {"create": False}
        if len(self.destination_picking_ids) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.destination_picking_ids.id
        return action
