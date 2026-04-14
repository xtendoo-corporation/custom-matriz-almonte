# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Modelo de líneas de importación PDA."""
from odoo import fields, models


class MatrizAlmontePdaSaleImportLine(models.Model):
    """Línea de detalle de una operación importada desde PDA."""

    _name = "matriz.almonte.pda.sale.import.line"
    _description = "Línea de Importación PDA"
    _order = "import_id, sequence, id"

    import_id = fields.Many2one(
        comodel_name="matriz.almonte.pda.sale.import",
        string="Importación",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(
        string="Secuencia",
        default=10,
    )
    product_code = fields.Char(
        string="Código Artículo",
        help="Código del artículo tal como lo envió la PDA.",
    )
    description = fields.Char(
        string="Descripción",
    )
    qty = fields.Float(
        string="Cantidad",
        digits=(16, 4),
    )
    unit_price = fields.Float(
        string="Precio Unitario",
        digits=(16, 4),
    )
    discount = fields.Float(
        string="Descuento (%)",
        digits=(5, 2),
        default=0.0,
    )
    line_total = fields.Float(
        string="Total Línea",
        digits=(16, 2),
    )

    # Campos preparados para fase 2 (mapeo con productos reales)
    # product_id = fields.Many2one('product.product', string='Producto Odoo', ...)
