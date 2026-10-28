from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    is_store_demand_central = fields.Boolean(
        string="Central de pre-pedidos",
        help="Almacén que recibe los pre-pedidos de las tiendas, compra a "
        "proveedor y reparte la mercancía. Solo puede haber uno por compañía.",
    )

    @api.constrains("is_store_demand_central", "company_id")
    def _check_unique_store_demand_central(self):
        for warehouse in self.filtered("is_store_demand_central"):
            duplicated = self.search_count([
                ("id", "!=", warehouse.id),
                ("company_id", "=", warehouse.company_id.id),
                ("is_store_demand_central", "=", True),
            ])
            if duplicated:
                raise ValidationError(_(
                    "Solo puede haber un almacén central de pre-pedidos por compañía."
                ))

    @api.model
    def _get_store_demand_central(self, company=None):
        company = company or self.env.company
        return self.search([
            ("company_id", "=", company.id),
            ("is_store_demand_central", "=", True),
        ], limit=1)
