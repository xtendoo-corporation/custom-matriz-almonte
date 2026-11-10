# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import api, fields, models

# Datos del envío de la PDA que acompañan a la cantidad contada pendiente y
# pasan al movimiento de ajuste al aplicarla.
PDA_COUNT_FIELDS = ("pda_count_start", "pda_count_end", "pda_count_comment")


class StockQuant(models.Model):
    _inherit = "stock.quant"

    pda_count_origin = fields.Char(
        string="Recuento PDA",
        readonly=True,
        copy=False,
        help="Envío de la PDA (número y PDA) del que viene la cantidad contada "
        "pendiente. Al aplicar, el movimiento de ajuste lleva esta referencia. "
        "Se borra si la cantidad contada se cambia a mano en Odoo.",
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

    @api.model
    def _pda_set_counted_quantities(self, location, counts, user, origin, info=None):
        """Registra el recuento de la PDA en el Inventario físico, sin aplicarlo.

        Por cada artículo deja su cantidad contada en el quant de ``location``
        (sin lote, paquete ni propietario), igual que al escribir la «Cantidad
        contada» en *Inventario > Inventario físico*. El ajuste lo aplica
        después alguien en Odoo con «Aplicar». Lo que no viene en ``counts`` se
        pone a cero aparte, con :meth:`_pda_zero_uncounted`.

        :param counts: ``{product: cantidad_contada}``.
        :param user: usuario asignado al recuento.
        :param origin: texto que identifica el envío (referencia del ajuste).
        :param info: ``{campo: valor}`` de ``PDA_COUNT_FIELDS`` (inicio, envío y
            comentario del recuento); los que falten quedan vacíos.
        :returns: los quants actualizados.
        """
        info = info or {}
        vals = {
            "inventory_quantity_set": True,
            "user_id": user.id,
            "pda_count_origin": origin,
        }
        vals.update({name: info.get(name) or False for name in PDA_COUNT_FIELDS})
        quants = self.browse()
        for product, qty in counts.items():
            quant = self._gather(product, location, strict=True).filtered(
                lambda q: not q.lot_id and not q.package_id and not q.owner_id
            )[:1]
            if not quant:
                quant = self.create({"product_id": product.id, "location_id": location.id})
            quant.with_context(pda_inventory_count=True).write(
                dict(vals, inventory_quantity=qty)
            )
            quants |= quant
        return quants

    @api.model
    def _pda_zero_uncounted(self, location, counted, keep_products, user, origin, info=None):
        """Pone a 0 (pendiente de aplicar) lo que la PDA no ha contado.

        Inventario completo: cualquier quant de ``location`` o sus
        sububicaciones con stock (o con un recuento pendiente) que no sea uno
        de ``counted`` queda con cantidad contada 0, de forma que al aplicar
        solo quede en stock lo inventariado.

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
                "|",
                ("quantity", "!=", 0),
                ("inventory_quantity_set", "=", True),
            ]
        )
        if quants:
            vals = {
                "inventory_quantity": 0,
                "inventory_quantity_set": True,
                "user_id": user.id,
                "pda_count_origin": origin,
            }
            vals.update({name: info.get(name) or False for name in PDA_COUNT_FIELDS})
            quants.with_context(pda_inventory_count=True).write(vals)
        return quants

    @staticmethod
    def _pda_clear_vals():
        return dict.fromkeys(("pda_count_origin",) + PDA_COUNT_FIELDS, False)

    def write(self, vals):
        # Un cambio manual de la cantidad contada deja de ser el recuento de
        # la PDA, salvo que se esté limpiando o aplicando el propio recuento.
        if (
            "inventory_quantity" in vals
            and "pda_count_origin" not in vals
            and not self.env.context.get("pda_inventory_count")
        ):
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

    def action_clear_inventory_quantity(self):
        res = super().action_clear_inventory_quantity()
        self.write(self._pda_clear_vals())
        return res
