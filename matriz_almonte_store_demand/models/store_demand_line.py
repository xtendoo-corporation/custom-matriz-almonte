from odoo import _, api, fields, models
from odoo.exceptions import UserError

CONSOLIDATION_FIELDS = {"partner_id", "purchase_line_id", "move_id"}


class StoreDemandLine(models.Model):
    _name = "matriz.store.demand.line"
    _description = "Línea de pre-pedido de tienda"
    _order = "demand_id desc, sequence, id"

    demand_id = fields.Many2one(
        "matriz.store.demand",
        string="Pre-pedido",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="demand_id.company_id", store=True)
    warehouse_id = fields.Many2one(
        related="demand_id.warehouse_id",
        string="Tienda",
        store=True,
    )
    central_warehouse_id = fields.Many2one(
        related="demand_id.central_warehouse_id",
        store=True,
    )
    state = fields.Selection(related="demand_id.state", store=True)
    date_request = fields.Datetime(related="demand_id.date_request", store=True)
    product_id = fields.Many2one(
        "product.product",
        string="Producto",
        required=True,
        check_company=True,
        domain="[('purchase_ok', '=', True), ('type', '=', 'consu')]",
    )
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unidad")
    product_qty = fields.Float(
        string="Cantidad",
        required=True,
        default=1.0,
        digits="Product Unit",
    )
    note = fields.Char(string="Nota")
    partner_id = fields.Many2one(
        "res.partner",
        string="Proveedor",
        compute="_compute_partner_id",
        store=True,
        readonly=False,
        check_company=True,
    )
    purchase_line_id = fields.Many2one(
        "purchase.order.line",
        string="Línea de compra",
        readonly=True,
        copy=False,
        index="btree_not_null",
        groups="purchase.group_purchase_user",
    )
    move_id = fields.Many2one(
        "stock.move",
        string="Movimiento de reparto",
        readonly=True,
        copy=False,
        index="btree_not_null",
        groups="stock.group_stock_user",
    )
    is_consolidated = fields.Boolean(
        string="Consolidada",
        compute="_compute_is_consolidated",
        store=True,
    )
    shop_qty_available = fields.Float(
        string="Stock tienda",
        compute="_compute_stock_quantities",
        digits="Product Unit",
    )
    central_free_qty = fields.Float(
        string="Disponible central",
        compute="_compute_stock_quantities",
        digits="Product Unit",
    )

    @api.depends("product_id")
    def _compute_partner_id(self):
        for line in self:
            seller = line.product_id.seller_ids.filtered(
                lambda info: not info.company_id or info.company_id == line.company_id
            )[:1]
            line.partner_id = seller.partner_id

    @api.depends("move_id")
    def _compute_is_consolidated(self):
        for line in self.sudo():
            line.is_consolidated = bool(line.move_id)

    @api.depends("product_id", "warehouse_id", "central_warehouse_id")
    def _compute_stock_quantities(self):
        # Solo informativo: la tienda no necesita permisos de inventario.
        for line in self:
            product = line.product_id.sudo()
            line.shop_qty_available = (
                product.with_context(warehouse_id=line.warehouse_id.id).qty_available
                if product and line.warehouse_id
                else 0.0
            )
            line.central_free_qty = (
                product.with_context(warehouse_id=line.central_warehouse_id.id).free_qty
                if product and line.central_warehouse_id
                else 0.0
            )

    def _check_demand_editable(self, demands, vals=None):
        if vals is not None and set(vals) <= CONSOLIDATION_FIELDS:
            return
        is_manager = self.env.user.has_group(
            "matriz_almonte_store_demand.group_store_demand_manager"
        )
        for demand in demands:
            if demand.state == "draft" or (is_manager and demand.state == "sent"):
                continue
            raise UserError(_(
                "No se pueden modificar las líneas del pre-pedido %(name)s en "
                "estado «%(state)s».",
                name=demand.name,
                state=dict(demand._fields["state"].selection)[demand.state],
            ))

    @api.model_create_multi
    def create(self, vals_list):
        demand_ids = {vals.get("demand_id") for vals in vals_list if vals.get("demand_id")}
        self._check_demand_editable(self.env["matriz.store.demand"].browse(demand_ids))
        return super().create(vals_list)

    def write(self, vals):
        if any(line.is_consolidated for line in self) and not (
            set(vals) <= CONSOLIDATION_FIELDS
        ):
            raise UserError(_("No se pueden modificar líneas ya consolidadas."))
        self._check_demand_editable(self.demand_id, vals)
        return super().write(vals)

    def unlink(self):
        self._check_demand_editable(self.demand_id)
        return super().unlink()

    def action_open_consolidate(self):
        return self.env["matriz.store.demand.consolidate"].action_open_for_lines(self)
