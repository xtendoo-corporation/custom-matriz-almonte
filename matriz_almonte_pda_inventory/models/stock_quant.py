# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import api, fields, models

# Datos de los envíos de la PDA que acompañan a la cantidad contada pendiente
# y pasan al movimiento de ajuste al aplicarla.
PDA_COUNT_FIELDS = ("pda_count_start", "pda_count_end", "pda_count_comment")


class StockQuant(models.Model):
    _inherit = "stock.quant"

    pda_count_origin = fields.Char(
        string="Inventario PDA",
        readonly=True,
        copy=False,
        index=True,
        help="Número del inventario de la PDA (INVENTORY/00045) al que pertenece la "
        "cantidad contada pendiente. Todas las líneas pendientes de una "
        "ubicación comparten número hasta que se aplican; al aplicar, el "
        "movimiento de ajuste lleva este número como referencia.",
    )
    pda_count_start = fields.Datetime(
        string="Inicio recuento PDA", readonly=True, copy=False
    )
    pda_count_end = fields.Datetime(
        string="Envío recuento PDA", readonly=True, copy=False
    )
    pda_count_comment = fields.Text(
        string="Comentario recuento PDA", readonly=True, copy=False
    )
    pda_log_ids = fields.One2many(
        "matriz.almonte.pda.inventory.log", "quant_id", string="Líneas recibidas de la PDA"
    )
    pda_log_count = fields.Integer(
        string="Envíos PDA", compute="_compute_pda_log_count"
    )

    @api.depends("pda_log_ids")
    def _compute_pda_log_count(self):
        data = self.env["matriz.almonte.pda.inventory.log"]._read_group(
            [("quant_id", "in", self.ids), ("name", "in", self.mapped("pda_count_origin"))],
            ["quant_id", "name"],
            ["__count"],
        )
        counts = {(quant.id, name): count for quant, name, count in data}
        for quant in self:
            quant.pda_log_count = counts.get((quant.id, quant.pda_count_origin), 0)

    def action_view_pda_logs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "matriz_almonte_pda_inventory.action_pda_inventory_log"
        )
        action["domain"] = [("quant_id", "=", self.id)]
        if self.pda_count_origin:
            action["domain"].append(("name", "=", self.pda_count_origin))
        action["context"] = {}
        return action

    # ------------------------------------------------------------------
    # Recepción desde la PDA
    # ------------------------------------------------------------------

    @api.model
    def _pda_pending_quants(self, location):
        return self.search(
            [
                ("location_id", "child_of", location.id),
                ("inventory_quantity_set", "=", True),
            ]
        )

    @api.model
    def _pda_current_inventory_number(self, location):
        """Número del inventario abierto en ``location``, si hay alguno.

        Un inventario está abierto mientras tenga alguna línea pendiente de
        aplicar con su número. Si conviven varios (no debería), se sigue con el
        último que recibió líneas.
        """
        quant = self.search(
            [
                ("location_id", "child_of", location.id),
                ("inventory_quantity_set", "=", True),
                ("pda_count_origin", "!=", False),
            ],
            order="write_date desc, id desc",
            limit=1,
        )
        return quant.pda_count_origin or False

    @staticmethod
    def _pda_merge_info(quant, info):
        """Inicio más antiguo, envío más reciente y comentarios acumulados."""
        start = min(filter(None, [quant.pda_count_start, info.get("pda_count_start")]), default=False)
        end = max(filter(None, [quant.pda_count_end, info.get("pda_count_end")]), default=False)
        comments = (quant.pda_count_comment or "").split("\n") if quant.pda_count_comment else []
        new_comment = info.get("pda_count_comment")
        if new_comment and new_comment not in comments:
            comments.append(new_comment)
        return {
            "pda_count_start": start,
            "pda_count_end": end,
            "pda_count_comment": "\n".join(comments) or False,
        }

    @api.model
    def _pda_add_counted_quantities(self, location, counts, user, number, info=None, log_vals=None):
        """Suma el recuento de la PDA al inventario ``number`` de ``location``.

        Por cada artículo, si ya tiene una cantidad contada pendiente de
        aplicar en ``location`` (sin lote, paquete ni propietario) se le suman
        las unidades recibidas; si no, las unidades pasan a ser su cantidad
        contada. Nada se aplica: alguien en Odoo pulsa «Aplicar». Cada línea
        recibida queda registrada en ``matriz.almonte.pda.inventory.log``.

        :param counts: ``{product: unidades_recibidas}``.
        :param number: número del inventario (INVENTORY/00045).
        :param info: inicio, envío y comentario de este envío.
        :param log_vals: valores comunes de las líneas del registro (PDA,
            uuid, referencias enviadas en ``refs`` por producto...).
        :returns: los quants actualizados.
        """
        info = info or {}
        log_vals = dict(log_vals or {})
        refs = log_vals.pop("refs", {})
        Log = self.env["matriz.almonte.pda.inventory.log"]
        quants = self.browse()
        logs = []
        for product, qty in counts.items():
            quant = self._gather(product, location, strict=True).filtered(
                lambda q: not q.lot_id and not q.package_id and not q.owner_id
            )[:1]
            if not quant:
                quant = self.create({"product_id": product.id, "location_id": location.id})
            counted = qty
            if quant.inventory_quantity_set:
                counted += quant.inventory_quantity
            vals = {
                "inventory_quantity": counted,
                "inventory_quantity_set": True,
                "user_id": user.id,
                "pda_count_origin": number,
            }
            vals.update(self._pda_merge_info(quant, info))
            quant.write(vals)
            quants |= quant
            logs.append(
                dict(
                    log_vals,
                    name=number,
                    quant_id=quant.id,
                    product_id=product.id,
                    id_articulo=refs.get(product.id, product.default_code),
                    location_id=location.id,
                    warehouse_id=location.warehouse_id.id,
                    company_id=quant.company_id.id or self.env.company.id,
                    quantity=qty,
                    counted_after=counted,
                    user_id=user.id,
                    **{name: info.get(name) or False for name in PDA_COUNT_FIELDS},
                )
            )
        Log.create(logs)
        return quants

    @api.model
    def _pda_zero_uncounted(self, location, counted, keep_products, user, number, info=None):
        """Pone a 0 (pendiente de aplicar) el stock que no está en el inventario.

        Inventario completo: todo quant de ``location`` o sus sububicaciones
        con stock que no esté ya pendiente de aplicar (es decir, que no forme
        parte del inventario abierto) y no sea uno de ``counted`` queda con
        cantidad contada 0 y el número del inventario. Si después llega en
        otro envío, sus unidades se suman a ese 0.

        No se tocan los artículos con lotes/números de serie (no se pueden
        contar desde la PDA) ni ``keep_products`` (enviados por la PDA pero
        que no se pudieron registrar).

        :returns: los quants puestos a 0.
        """
        info = info or {}
        quants = self.search(
            [
                ("location_id", "child_of", location.id),
                ("id", "not in", counted.ids),
                ("product_id", "not in", keep_products.ids),
                ("product_id.tracking", "=", "none"),
                ("inventory_quantity_set", "=", False),
                ("quantity", "!=", 0),
            ]
        )
        if quants:
            vals = {
                "inventory_quantity": 0,
                "inventory_quantity_set": True,
                "user_id": user.id,
                "pda_count_origin": number,
            }
            vals.update({name: info.get(name) or False for name in PDA_COUNT_FIELDS})
            quants.write(vals)
        return quants

    # ------------------------------------------------------------------
    # Ciclo de vida del recuento
    # ------------------------------------------------------------------

    @staticmethod
    def _pda_clear_vals():
        return dict.fromkeys(("pda_count_origin",) + PDA_COUNT_FIELDS, False)

    def write(self, vals):
        # Cuando la línea deja de estar pendiente (se aplica o se borra el
        # recuento) deja de pertenecer al inventario de la PDA.
        if "inventory_quantity_set" in vals and not vals["inventory_quantity_set"]:
            vals = dict(vals, **self._pda_clear_vals())
        return super().write(vals)

    def _get_inventory_move_values(self, qty, location_id, location_dest_id, package_id=False, package_dest_id=False):
        res = super()._get_inventory_move_values(
            qty, location_id, location_dest_id,
            package_id=package_id, package_dest_id=package_dest_id,
        )
        if self.pda_count_origin:
            res["inventory_name"] = self.pda_count_origin
            res.update({name: self[name] for name in PDA_COUNT_FIELDS})
        return res
