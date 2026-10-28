from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class StoreDemandConsolidate(models.TransientModel):
    _name = "matriz.store.demand.consolidate"
    _description = "Consolidar demanda de tiendas"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    central_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Almacén central", required=True, readonly=True
    )
    use_central_stock = fields.Boolean(
        string="Descontar stock libre de central",
        default=True,
        help="Compra solo lo que falta tras servir con el stock libre del "
        "almacén central. Si se desmarca, se compra toda la demanda.",
    )
    line_ids = fields.One2many(
        "matriz.store.demand.consolidate.line", "wizard_id", string="Productos"
    )
    demand_count = fields.Integer(
        string="Pre-pedidos", compute="_compute_summary"
    )
    purchase_partner_count = fields.Integer(
        string="Pedidos de compra a generar", compute="_compute_summary"
    )

    @api.depends("line_ids.demand_line_ids", "line_ids.purchase_qty", "line_ids.partner_id")
    def _compute_summary(self):
        for wizard in self:
            wizard.demand_count = len(wizard.line_ids.demand_line_ids.demand_id)
            wizard.purchase_partner_count = len(
                wizard.line_ids.filtered(lambda line: line.purchase_qty > 0).partner_id
            )

    @api.onchange("use_central_stock")
    def _onchange_use_central_stock(self):
        for line in self.line_ids:
            line.purchase_qty = line._get_default_purchase_qty(self.use_central_stock)

    @api.model
    def _get_pending_demand_lines(self, demand_lines=None):
        domain = [
            ("state", "=", "sent"),
            ("is_consolidated", "=", False),
            ("product_qty", ">", 0),
            ("company_id", "=", self.env.company.id),
        ]
        if demand_lines is not None:
            domain.append(("id", "in", demand_lines.ids))
        return self.env["matriz.store.demand.line"].search(domain)

    @api.model
    def action_open_for_lines(self, demand_lines=None):
        pending = self._get_pending_demand_lines(demand_lines)
        if not pending:
            raise UserError(_("No hay líneas de pre-pedido enviadas pendientes de consolidar."))
        central = pending.central_warehouse_id
        if len(central) > 1:
            raise UserError(_(
                "Las líneas seleccionadas pertenecen a varios almacenes centrales; "
                "consolida cada almacén por separado."
            ))
        wizard = self.create({
            "central_warehouse_id": central.id,
            "line_ids": self._prepare_wizard_line_commands(pending, central),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Consolidar demanda"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    @api.model
    def action_open_all(self):
        return self.action_open_for_lines()

    @api.model
    def _prepare_wizard_line_commands(self, demand_lines, central):
        grouped = defaultdict(lambda: self.env["matriz.store.demand.line"])
        for line in demand_lines:
            grouped[(line.product_id, line.partner_id)] |= line
        commands = []
        for (product, partner), lines in grouped.items():
            demand_qty = sum(lines.mapped("product_qty"))
            free_qty = max(
                product.with_context(warehouse_id=central.id).free_qty, 0.0
            )
            commands.append(Command.create({
                "product_id": product.id,
                "partner_id": partner.id,
                "demand_qty": demand_qty,
                "central_free_qty": free_qty,
                "purchase_qty": max(demand_qty - free_qty, 0.0),
                "demand_line_ids": [Command.set(lines.ids)],
            }))
        return commands

    def action_consolidate(self):
        self.ensure_one()
        demand_lines = self.line_ids.demand_line_ids
        stale = demand_lines.filtered(
            lambda line: line.state != "sent" or line.is_consolidated
        )
        if stale:
            raise UserError(_(
                "Algunas líneas ya no están pendientes (otro usuario las ha "
                "consolidado o el pre-pedido ha cambiado). Vuelve a abrir la "
                "consolidación."
            ))
        lines_to_buy = self.line_ids.filtered(lambda line: line.purchase_qty > 0)
        missing_partner = lines_to_buy.filtered(lambda line: not line.partner_id)
        if missing_partner:
            raise UserError(_(
                "Indica el proveedor de estos productos antes de consolidar:\n%s",
                "\n".join(missing_partner.product_id.mapped("display_name")),
            ))
        for line in self.line_ids:
            line.demand_line_ids.write({"partner_id": line.partner_id.id})
        purchases = self._create_purchase_orders(lines_to_buy)
        pickings = self._create_transfers(demand_lines)
        demand_lines.demand_id._update_state_from_lines()
        return self._get_result_action(purchases, pickings)

    def _get_origin(self, demand_lines):
        return ", ".join(demand_lines.demand_id.sorted("name").mapped("name"))

    def _create_purchase_orders(self, lines_to_buy):
        purchase_order_model = self.env["purchase.order"]
        purchase_line_model = self.env["purchase.order.line"]
        purchases = purchase_order_model
        for partner, wizard_lines in lines_to_buy.grouped("partner_id").items():
            purchase = purchase_order_model.create({
                "partner_id": partner.id,
                "company_id": self.company_id.id,
                "picking_type_id": self.central_warehouse_id.in_type_id.id,
                "origin": self._get_origin(wizard_lines.demand_line_ids),
            })
            for wizard_line in wizard_lines:
                product = wizard_line.product_id
                purchase_line = purchase_line_model.create(
                    purchase_line_model._prepare_purchase_order_line(
                        product,
                        wizard_line.purchase_qty,
                        product.uom_id,
                        self.company_id,
                        partner,
                        purchase,
                    )
                )
                wizard_line.demand_line_ids.write({"purchase_line_id": purchase_line.id})
            purchases |= purchase
        return purchases

    def _create_transfers(self, demand_lines):
        central = self.central_warehouse_id
        picking_model = self.env["stock.picking"]
        move_model = self.env["stock.move"]
        pickings = picking_model
        for shop, shop_lines in demand_lines.grouped("warehouse_id").items():
            picking = picking_model.create({
                "picking_type_id": central.int_type_id.id,
                "location_id": central.lot_stock_id.id,
                "location_dest_id": shop.lot_stock_id.id,
                "partner_id": shop.partner_id.id,
                "origin": self._get_origin(shop_lines),
                "company_id": self.company_id.id,
            })
            for demand_line in shop_lines:
                move = move_model.create({
                    "picking_id": picking.id,
                    "product_id": demand_line.product_id.id,
                    "product_uom": demand_line.product_id.uom_id.id,
                    "product_uom_qty": demand_line.product_qty,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "origin": demand_line.demand_id.name,
                    "company_id": self.company_id.id,
                })
                demand_line.write({"move_id": move.id})
            picking.action_confirm()
            picking.action_assign()
            pickings |= picking
        return pickings

    def _get_result_action(self, purchases, pickings):
        if purchases:
            action = self.env["ir.actions.act_window"]._for_xml_id("purchase.purchase_rfq")
            action["domain"] = [("id", "in", purchases.ids)]
        else:
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "stock.action_picking_tree_all"
            )
            action["domain"] = [("id", "in", pickings.ids)]
        action["context"] = {"create": False}
        return action


class StoreDemandConsolidateLine(models.TransientModel):
    _name = "matriz.store.demand.consolidate.line"
    _description = "Producto a consolidar"
    _order = "partner_id, product_id"

    wizard_id = fields.Many2one(
        "matriz.store.demand.consolidate", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("product.product", string="Producto", readonly=True)
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unidad")
    partner_id = fields.Many2one("res.partner", string="Proveedor")
    demand_qty = fields.Float(
        string="Demanda tiendas", readonly=True, digits="Product Unit"
    )
    central_free_qty = fields.Float(
        string="Libre en central", readonly=True, digits="Product Unit"
    )
    purchase_qty = fields.Float(string="A comprar", digits="Product Unit")
    demand_line_ids = fields.Many2many(
        "matriz.store.demand.line",
        "matriz_store_demand_consolidate_line_rel",
        "wizard_line_id",
        "demand_line_id",
        string="Líneas de pre-pedido",
        readonly=True,
    )
    shop_names = fields.Char(string="Tiendas", compute="_compute_shop_names")

    @api.depends("demand_line_ids")
    def _compute_shop_names(self):
        for line in self:
            line.shop_names = ", ".join(
                f"{warehouse.code}: {sum(lines.mapped('product_qty')):g}"
                for warehouse, lines in line.demand_line_ids.grouped("warehouse_id").items()
            )

    def _get_default_purchase_qty(self, use_central_stock):
        self.ensure_one()
        if not use_central_stock:
            return self.demand_qty
        return max(self.demand_qty - self.central_free_qty, 0.0)
