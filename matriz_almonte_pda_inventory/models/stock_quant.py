# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import api, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    @api.model
    def _pda_set_counted_quantities(self, location, counts, user):
        """Registra el recuento de la PDA en el Inventario físico, sin aplicarlo.

        Por cada artículo deja su cantidad contada en el quant de ``location``
        (sin lote, paquete ni propietario), igual que al escribir la «Cantidad
        contada» en *Inventario > Inventario físico*. El ajuste lo aplica
        después alguien en Odoo con «Aplicar». Los artículos que no vienen en
        ``counts`` no se tocan.

        :param counts: ``{product: cantidad_contada}``.
        :param user: usuario asignado al recuento.
        :returns: los quants actualizados.
        """
        quants = self.browse()
        for product, qty in counts.items():
            quant = self._gather(product, location, strict=True).filtered(
                lambda q: not q.lot_id and not q.package_id and not q.owner_id
            )[:1]
            if not quant:
                quant = self.create({"product_id": product.id, "location_id": location.id})
            quant.write(
                {
                    "inventory_quantity": qty,
                    "inventory_quantity_set": True,
                    "user_id": user.id,
                }
            )
            quants |= quant
        return quants
