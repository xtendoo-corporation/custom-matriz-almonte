# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

ZERO_COMMENT = "No estaba en el inventario: puesto a 0."


class MatrizAlmontePdaInventoryZeroing(models.Model):
    """Puesta a 0 del stock que no estaba en un inventario de la PDA.

    Se crea cuando se aplica la última línea pendiente de un inventario
    (INVENTORY/00045) y la tarea programada «PDA: poner a 0 lo no
    inventariado» pone a 0 y aplica, por tandas, todo el stock de la ubicación
    del inventario (y sus sububicaciones) cuyos artículos no estaban en él.
    """

    _name = "matriz.almonte.pda.inventory.zeroing"
    _description = "Puesta a 0 tras un inventario de la PDA"
    _order = "id desc"

    _BATCH = 300

    name = fields.Char(string="Inventario", required=True, readonly=True, index=True)
    location_id = fields.Many2one(
        "stock.location", string="Ubicación", required=True, readonly=True,
        ondelete="cascade",
    )
    warehouse_id = fields.Many2one(
        related="location_id.warehouse_id", string="Almacén", store=True
    )
    company_id = fields.Many2one(
        "res.company", string="Compañía", readonly=True,
        default=lambda self: self.env.company,
    )
    counted_product_ids = fields.Many2many(
        "product.product", string="Artículos del inventario", readonly=True,
        help="Artículos que estaban en el inventario: su línea contada no se "
        "pone a 0 (sí su stock en sububicaciones).",
    )
    state = fields.Selection(
        [("pending", "Pendiente"), ("done", "Hecho"), ("error", "Error")],
        string="Estado", default="pending", required=True, readonly=True,
        index=True,
    )
    zeroed_count = fields.Integer(string="Líneas puestas a 0", readonly=True)
    date_done = fields.Datetime(string="Terminado", readonly=True)
    error = fields.Text(readonly=True)

    _name_location_uniq = models.Constraint(
        "UNIQUE(name, location_id)",
        "Ya existe la puesta a 0 de este inventario en esta ubicación.",
    )

    # ------------------------------------------------------------------
    # Apertura (al aplicar la última línea del inventario)
    # ------------------------------------------------------------------

    @api.model
    def _pda_inventory_applied(self, applied):
        """Abre la puesta a 0 de cada inventario que ha quedado aplicado entero.

        :param applied: ``{número: quants aplicados con ese número}``.
        """
        Quant = self.env["stock.quant"].sudo()
        Log = self.env["matriz.almonte.pda.inventory.log"].sudo()
        created = self.browse()
        for number, quants in applied.items():
            logs = Log.search([("name", "=", number)])
            location = logs.location_id[:1] or quants.location_id.warehouse_id.lot_stock_id[:1]
            if not location:
                continue
            still_pending = Quant.search_count(
                [
                    ("location_id", "child_of", location.id),
                    ("inventory_quantity_set", "=", True),
                    ("pda_count_origin", "=", number),
                ],
                limit=1,
            )
            if still_pending:
                continue
            if self.sudo().search_count(
                [("name", "=", number), ("location_id", "=", location.id)], limit=1
            ):
                continue
            created |= self.sudo().create(
                {
                    "name": number,
                    "location_id": location.id,
                    "company_id": (location.company_id or self.env.company).id,
                    "counted_product_ids": [
                        (6, 0, (logs.product_id | quants.product_id).ids)
                    ],
                }
            )
            _logger.info(
                "PDA inventario %s aplicado entero en %s: puesta a 0 pendiente",
                number,
                location.complete_name,
            )
        if created:
            self.env.ref(
                "matriz_almonte_pda_inventory.ir_cron_pda_inventory_zeroing"
            ).sudo()._trigger()
        return created

    # ------------------------------------------------------------------
    # Proceso (tarea programada)
    # ------------------------------------------------------------------

    def _quants_to_zero(self, limit=None):
        self.ensure_one()
        return (
            self.env["stock.quant"]
            .sudo()
            .with_company(self.company_id)
            .search(
                [
                    ("location_id", "child_of", self.location_id.id),
                    # Solo se respeta la línea contada de cada artículo (en la
                    # ubicación del inventario, sin paquete ni propietario); su
                    # stock en sububicaciones o paquetes también pasa a 0.
                    "|", "|", "|",
                    ("product_id", "not in", self.counted_product_ids.ids),
                    ("location_id", "!=", self.location_id.id),
                    ("package_id", "!=", False),
                    ("owner_id", "!=", False),
                    ("product_id.tracking", "=", "none"),
                    # Una línea pendiente es un recuento en curso: no se toca.
                    ("inventory_quantity_set", "=", False),
                    ("quantity", "!=", 0),
                ],
                limit=limit,
                order="id",
            )
        )

    def _process_batch(self, limit=None):
        """Pone a 0 y aplica una tanda. Devuelve True si ha terminado."""
        self.ensure_one()
        limit = limit or self._BATCH
        try:
            with self.env.cr.savepoint():
                quants = self._quants_to_zero(limit=limit)
                if quants:
                    quants.write(
                        {
                            "inventory_quantity": 0,
                            "inventory_quantity_set": True,
                            "user_id": False,
                            "pda_count_origin": self.name,
                            "pda_count_start": False,
                            "pda_count_end": False,
                            "pda_count_comment": ZERO_COMMENT,
                        }
                    )
                    quants.with_context(pda_inventory_zeroing=True)._apply_inventory()
        except Exception as exc:  # noqa: BLE001 - se guarda para revisarlo
            _logger.exception("PDA inventario %s: error al poner a 0", self.name)
            self.write({"state": "error", "error": str(exc)})
            return True
        done = len(quants) < limit
        vals = {"zeroed_count": self.zeroed_count + len(quants), "error": False}
        if done:
            vals.update(state="done", date_done=fields.Datetime.now())
            _logger.info(
                "PDA inventario %s: puestas a 0 %s líneas en %s",
                self.name,
                vals["zeroed_count"],
                self.location_id.complete_name,
            )
        self.write(vals)
        return done

    @api.model
    def _cron_process(self, limit=None):
        """Procesa una tanda de la puesta a 0 más antigua pendiente.

        Si queda trabajo, vuelve a lanzar la tarea en cuanto termina, de modo
        que cada ejecución es corta y no bloquea al resto de Odoo.
        """
        pending = self.search([("state", "=", "pending")], order="id", limit=1)
        if not pending:
            return
        pending._process_batch(limit=limit)
        if self.search_count([("state", "=", "pending")], limit=1):
            self.env.ref(
                "matriz_almonte_pda_inventory.ir_cron_pda_inventory_zeroing"
            )._trigger()

    def action_retry(self):
        self.filtered(lambda z: z.state == "error").write(
            {"state": "pending", "error": False}
        )
        self.env.ref(
            "matriz_almonte_pda_inventory.ir_cron_pda_inventory_zeroing"
        ).sudo()._trigger()
