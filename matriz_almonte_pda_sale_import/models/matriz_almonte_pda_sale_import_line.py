# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Modelo de líneas de importación PDA."""
from odoo import api, fields, models


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
        string="Nº Línea",
        default=0,
        help="Número de línea tal como lo envió la PDA (campo 'numero' del JSON).",
    )
    product_code = fields.Char(
        string="Cód. Artículo",
        help="Código del artículo (campo 'id_articulo' del JSON de la PDA).",
    )
    description = fields.Char(
        string="Descripción",
    )
    qty = fields.Float(
        string="Unidades",
        digits=(16, 4),
        help="Cantidad enviada por la PDA (campo 'unidades'). Puede ser negativa.",
    )
    unit_price = fields.Float(
        string="Precio",
        digits=(16, 4),
        help="Precio unitario (campo 'precio' del JSON de la PDA).",
    )
    discount = fields.Float(
        string="Descuento (%)",
        digits=(5, 2),
        default=0.0,
    )
    line_uuid = fields.Char(
        string="UUID Línea",
        readonly=True,
        copy=False,
        help="UUID de la línea enviado por la PDA (campo 'uuid' dentro de cada línea).",
    )
    line_total = fields.Float(
        string="Total Línea",
        digits=(16, 2),
        compute="_compute_line_total",
        store=True,
        help="Calculado: unidades × precio × (1 - descuento/100).",
    )
    tienda_id = fields.Many2one(
        related='import_id.tienda_id', 
        string='Tienda',
        store=True,
        help='Tienda a la que pertenece esta línea de importación.'
    )

    @api.depends("qty", "unit_price", "discount")
    def _compute_line_total(self):
        for line in self:
            line.line_total = line.qty * line.unit_price * (1.0 - line.discount / 100.0)

    # Campos preparados para fase 2 (mapeo con productos reales)
    # product_id = fields.Many2one('product.product', string='Producto Odoo', ...)
