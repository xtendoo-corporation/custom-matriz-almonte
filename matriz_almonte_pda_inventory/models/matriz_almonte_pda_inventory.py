# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Inventarios completos recibidos desde la PDA de una tienda.

Cada registro guarda el recuento enviado por la PDA y el resultado de
aplicarlo: el stock teórico que había, lo contado y el ajuste realizado.

Reglas del inventario (completo, aplicado al recibir):

* Ubicación: la de existencias del almacén del punto de venta del token
  (``lot_stock_id``) y todas sus sububicaciones.
* Artículo contado: su stock en el almacén pasa a ser lo contado. La cantidad
  se deja en la ubicación de existencias y cualquier otra ubicación, paquete o
  propietario de ese artículo dentro del almacén queda a 0.
* Artículo NO contado con stock distinto de 0: se pone a 0.
* Artículo con lotes o números de serie contado: la PDA no indica el lote, así
  que no se ajusta y se avisa para revisarlo en Odoo.
"""

from odoo import api, fields, models

SKIP_TRACKED = "Artículo con lotes/números de serie: revisar el inventario en Odoo."
SKIP_NOT_STORABLE = "Artículo sin control de stock: no se ajusta."


class MatrizAlmontePdaInventory(models.Model):
    _name = "matriz.almonte.pda.inventory"
    _description = "Inventario PDA"
    _order = "date desc, id desc"

    name = fields.Char(
        string="Referencia", required=True, readonly=True, copy=False, default="/"
    )
    pda_uuid = fields.Char(
        string="UUID PDA",
        required=True,
        readonly=True,
        copy=False,
        index=True,
        help="Identificador único generado por la PDA; evita aplicar dos veces "
        "el mismo inventario si la PDA reenvía.",
    )
    date = fields.Datetime(
        string="Fecha", required=True, readonly=True, default=fields.Datetime.now
    )
    token_id = fields.Many2one(
        "matriz.almonte.api.token", string="PDA", readonly=True, ondelete="restrict"
    )
    user_id = fields.Many2one("res.users", string="Usuario", readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Almacén", required=True, readonly=True
    )
    location_id = fields.Many2one(
        "stock.location", string="Ubicación", required=True, readonly=True
    )
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True, readonly=True
    )
    note = fields.Text(string="Nota", readonly=True)
    line_ids = fields.One2many(
        "matriz.almonte.pda.inventory.line",
        "inventory_id",
        string="Líneas",
        readonly=True,
    )
    counted_count = fields.Integer(
        string="Artículos contados", compute="_compute_counts", store=True
    )
    adjusted_count = fields.Integer(
        string="Artículos ajustados", compute="_compute_counts", store=True
    )
    zeroed_count = fields.Integer(
        string="Artículos puestos a 0", compute="_compute_counts", store=True
    )
    skipped_count = fields.Integer(
        string="Artículos sin ajustar", compute="_compute_counts", store=True
    )
    move_count = fields.Integer(string="Movimientos", compute="_compute_move_count")

    _pda_uuid_company_unique = models.Constraint(
        "UNIQUE(company_id, pda_uuid)",
        "Ya existe un inventario PDA con ese UUID.",
    )

    @api.depends("line_ids.counted", "line_ids.state")
    def _compute_counts(self):
        for inventory in self:
            lines = inventory.line_ids
            inventory.counted_count = len(lines.filtered("counted"))
            inventory.adjusted_count = len(
                lines.filtered(lambda line: line.state == "adjusted")
            )
            inventory.zeroed_count = len(
                lines.filtered(
                    lambda line: line.state == "adjusted" and not line.counted
                )
            )
            inventory.skipped_count = len(
                lines.filtered(lambda line: line.state == "skipped")
            )

    def _inventory_moves(self):
        self.ensure_one()
        return self.env["stock.move"].search(
            [
                ("is_inventory", "=", True),
                ("inventory_name", "=", self.name),
                ("company_id", "=", self.company_id.id),
            ]
        )

    def _compute_move_count(self):
        for inventory in self:
            inventory.move_count = len(inventory._inventory_moves())

    def action_view_moves(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.stock_move_action"
        )
        action["domain"] = [("id", "in", self._inventory_moves().ids)]
        action["context"] = {"create": False}
        return action

    # ------------------------------------------------------------------
    # Aplicación del recuento
    # ------------------------------------------------------------------

    @api.model
    def _apply_pda_count(self, token, warehouse, uuid, counts, note=False):
        """Crea el inventario y aplica el recuento en el almacén.

        :param counts: ``{product: (cantidad_contada, referencia_pda)}`` con
            los artículos ya resueltos y las unidades agrupadas.
        :returns: el registro ``matriz.almonte.pda.inventory`` creado.
        """
        company = warehouse.company_id
        location = warehouse.lot_stock_id
        inventory = self.create(
            {
                "name": self.env["ir.sequence"]
                .with_company(company)
                .next_by_code("matriz.almonte.pda.inventory")
                or "/",
                "pda_uuid": uuid,
                "token_id": token.id,
                "user_id": token.sale_user_id.id,
                "warehouse_id": warehouse.id,
                "location_id": location.id,
                "company_id": company.id,
                "note": note or False,
            }
        )

        Quant = self.env["stock.quant"].with_company(company)
        quants = Quant.search(
            [
                ("location_id", "child_of", location.id),
                ("product_id.is_storable", "=", True),
            ]
        )
        quants_by_product = {}
        for quant in quants:
            quants_by_product.setdefault(quant.product_id, Quant)
            quants_by_product[quant.product_id] |= quant

        products = self.env["product.product"].browse(
            {p.id for p in counts} | {p.id for p in quants_by_product}
        )
        line_vals = []
        to_apply = Quant
        for product in products.sorted(lambda p: (p.default_code or "", p.id)):
            product_quants = quants_by_product.get(product, Quant)
            theoretical = sum(product_quants.mapped("quantity"))
            counted_qty, ref = counts.get(product, (None, False))
            counted = counted_qty is not None
            vals = {
                "product_id": product.id,
                "pda_ref": ref,
                "counted": counted,
                "theoretical_qty": theoretical,
                "counted_qty": counted_qty or 0.0,
            }
            if counted and not product.is_storable:
                line_vals.append(
                    dict(vals, state="skipped", skip_reason=SKIP_NOT_STORABLE)
                )
                continue
            if counted and product.tracking != "none":
                line_vals.append(dict(vals, state="skipped", skip_reason=SKIP_TRACKED))
                continue

            targets = {quant: 0.0 for quant in product_quants}
            if counted:
                main_quant = product_quants.filtered(
                    lambda q: q.location_id == location
                    and not q.lot_id
                    and not q.package_id
                    and not q.owner_id
                )[:1]
                if not main_quant and product.uom_id.compare(counted_qty, 0):
                    main_quant = Quant.create(
                        {"product_id": product.id, "location_id": location.id}
                    )
                if main_quant:
                    targets[main_quant] = counted_qty
            changed = Quant
            for quant, target in targets.items():
                if product.uom_id.compare(quant.quantity, target):
                    quant.write(
                        {"inventory_quantity": target, "inventory_quantity_set": True}
                    )
                    changed |= quant
            if not changed and not counted:
                continue
            to_apply |= changed
            line_vals.append(dict(vals, state="adjusted" if changed else "no_change"))

        inventory.line_ids = [fields.Command.create(vals) for vals in line_vals]
        if to_apply:
            # La diferencia guardada no depende de ``quantity``: si el quant
            # tenía un recuento pendiente previo podría estar desfasada.
            to_apply._compute_inventory_diff_quantity()
            to_apply.with_context(inventory_name=inventory.name)._apply_inventory()
        return inventory


class MatrizAlmontePdaInventoryLine(models.Model):
    _name = "matriz.almonte.pda.inventory.line"
    _description = "Línea de inventario PDA"
    _order = "inventory_id, id"

    inventory_id = fields.Many2one(
        "matriz.almonte.pda.inventory",
        string="Inventario",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one(
        "product.product", string="Artículo", required=True, readonly=True
    )
    pda_ref = fields.Char(
        string="Referencia PDA",
        readonly=True,
        help="Referencia enviada por la PDA (vacía si el artículo no se contó).",
    )
    counted = fields.Boolean(
        string="Contado",
        readonly=True,
        help="El artículo venía en el recuento de la PDA. Si no, se puso a 0.",
    )
    theoretical_qty = fields.Float(
        string="Stock anterior", digits="Product Unit", readonly=True
    )
    counted_qty = fields.Float(string="Cantidad contada", digits="Product Unit", readonly=True)
    difference_qty = fields.Float(
        string="Diferencia",
        digits="Product Unit",
        compute="_compute_difference_qty",
        store=True,
    )
    state = fields.Selection(
        [
            ("adjusted", "Ajustado"),
            ("no_change", "Sin cambios"),
            ("skipped", "No ajustado"),
        ],
        string="Resultado",
        readonly=True,
    )
    skip_reason = fields.Char(string="Motivo", readonly=True)

    @api.depends("counted_qty", "theoretical_qty", "state")
    def _compute_difference_qty(self):
        for line in self:
            line.difference_qty = (
                0.0
                if line.state == "skipped"
                else line.counted_qty - line.theoretical_qty
            )
