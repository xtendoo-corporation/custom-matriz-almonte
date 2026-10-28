from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StoreDemand(models.Model):
    _name = "matriz.store.demand"
    _description = "Pre-pedido de tienda"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_request desc, id desc"

    name = fields.Char(
        string="Referencia",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("Nuevo"),
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Tienda",
        required=True,
        tracking=True,
        check_company=True,
        domain="[('is_store_demand_central', '=', False)]",
        default=lambda self: self._default_warehouse_id(),
    )
    central_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Almacén central",
        required=True,
        check_company=True,
        domain="[('is_store_demand_central', '=', True)]",
        default=lambda self: self.env["stock.warehouse"]._get_store_demand_central(),
    )
    user_id = fields.Many2one(
        "res.users",
        string="Solicitado por",
        required=True,
        default=lambda self: self.env.user,
    )
    date_request = fields.Datetime(
        string="Fecha",
        required=True,
        default=fields.Datetime.now,
        copy=False,
    )
    date_expected = fields.Date(string="Fecha deseada")
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("sent", "Enviado"),
            ("consolidated", "Consolidado"),
            ("done", "Servido"),
            ("cancel", "Cancelado"),
        ],
        string="Estado",
        required=True,
        default="draft",
        copy=False,
        index=True,
        tracking=True,
    )
    note = fields.Text(string="Observaciones")
    line_ids = fields.One2many(
        "matriz.store.demand.line",
        "demand_id",
        string="Líneas",
        copy=True,
    )
    line_count = fields.Integer(string="Nº líneas", compute="_compute_line_totals")
    total_qty = fields.Float(
        string="Unidades",
        compute="_compute_line_totals",
        digits="Product Unit",
    )
    picking_ids = fields.Many2many(
        "stock.picking",
        string="Traspasos",
        compute="_compute_picking_ids",
        groups="stock.group_stock_user",
    )
    picking_count = fields.Integer(
        compute="_compute_picking_ids",
        groups="stock.group_stock_user",
    )
    purchase_ids = fields.Many2many(
        "purchase.order",
        string="Pedidos de compra",
        compute="_compute_purchase_ids",
        groups="purchase.group_purchase_user",
    )
    purchase_count = fields.Integer(
        compute="_compute_purchase_ids",
        groups="purchase.group_purchase_user",
    )

    @api.model
    def _default_warehouse_id(self):
        warehouse = self.env.user.property_warehouse_id
        if warehouse and not warehouse.is_store_demand_central:
            return warehouse
        return False

    @api.depends("line_ids.product_qty")
    def _compute_line_totals(self):
        for demand in self:
            demand.line_count = len(demand.line_ids)
            demand.total_qty = sum(demand.line_ids.mapped("product_qty"))

    @api.depends("line_ids.move_id")
    def _compute_picking_ids(self):
        for demand in self:
            demand.picking_ids = demand.line_ids.move_id.picking_id
            demand.picking_count = len(demand.picking_ids)

    @api.depends("line_ids.purchase_line_id")
    def _compute_purchase_ids(self):
        for demand in self:
            demand.purchase_ids = demand.line_ids.purchase_line_id.order_id
            demand.purchase_count = len(demand.purchase_ids)

    @api.constrains("warehouse_id", "central_warehouse_id")
    def _check_warehouses(self):
        for demand in self:
            if demand.warehouse_id == demand.central_warehouse_id:
                raise ValidationError(_(
                    "La tienda y el almacén central del pre-pedido deben ser distintos."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "matriz.store.demand"
                ) or _("Nuevo")
        return super().create(vals_list)

    def unlink(self):
        if any(demand.state not in ("draft", "cancel") for demand in self):
            raise UserError(_(
                "Solo se pueden eliminar pre-pedidos en borrador o cancelados."
            ))
        return super().unlink()

    def action_send(self):
        for demand in self:
            if demand.state != "draft":
                raise UserError(_(
                    "Solo se pueden enviar pre-pedidos en borrador (%s).", demand.name
                ))
            if not demand.line_ids:
                raise UserError(_(
                    "El pre-pedido %s no tiene líneas.", demand.name
                ))
            if any(line.product_qty <= 0 for line in demand.line_ids):
                raise UserError(_(
                    "Todas las líneas del pre-pedido %s deben tener cantidad mayor que cero.",
                    demand.name,
                ))
        self.write({"state": "sent", "date_request": fields.Datetime.now()})

    def action_draft(self):
        self._check_not_consolidated()
        self.write({"state": "draft"})

    def action_cancel(self):
        self._check_not_consolidated()
        self.write({"state": "cancel"})

    def _check_not_consolidated(self):
        for demand in self:
            if demand.state not in ("draft", "sent", "cancel") or any(
                demand.line_ids.mapped("is_consolidated")
            ):
                raise UserError(_(
                    "El pre-pedido %s ya se ha consolidado en central y no se "
                    "puede modificar.",
                    demand.name,
                ))

    def _update_state_from_lines(self):
        """Avanza el estado según el avance de consolidación y reparto.

        El pre-pedido queda «Servido» cuando sus traspasos, incluidas las
        entregas pendientes (backorders), están hechos o cancelados.
        """
        for demand in self.filtered(lambda d: d.state in ("sent", "consolidated")):
            lines = demand.sudo().line_ids
            if not lines or not all(lines.mapped("is_consolidated")):
                continue
            pickings = lines.move_id.picking_id
            backorders = pickings.backorder_ids
            while backorders - pickings:
                pickings |= backorders
                backorders = pickings.backorder_ids
            if pickings and all(
                picking.state in ("done", "cancel") for picking in pickings
            ) and any(picking.state == "done" for picking in pickings):
                demand.state = "done"
            else:
                demand.state = "consolidated"

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        action["domain"] = [("id", "in", self.picking_ids.ids)]
        action["context"] = {"create": False}
        return action

    def action_view_purchases(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "purchase.purchase_rfq"
        )
        action["domain"] = [("id", "in", self.purchase_ids.ids)]
        action["context"] = {"create": False}
        return action
