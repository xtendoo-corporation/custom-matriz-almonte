# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import fields, models


class MatrizAlmontePdaInventoryLog(models.Model):
    """Cada línea de inventario recibida desde una PDA.

    Una fila por artículo y envío: si la cantidad contada de un artículo es 10
    porque llegaron dos envíos de 5, aquí hay dos filas. Solo se escribe desde
    la API de la PDA; en Odoo es de consulta.
    """

    _name = "matriz.almonte.pda.inventory.log"
    _description = "Línea de inventario recibida desde la PDA"
    _order = "id desc"

    name = fields.Char(
        string="Inventario",
        required=True,
        readonly=True,
        index=True,
        help="Número del inventario (INVENTORY/00045) al que se sumó la línea.",
    )
    quant_id = fields.Many2one(
        "stock.quant",
        string="Línea de inventario físico",
        readonly=True,
        index=True,
        ondelete="set null",
    )
    product_id = fields.Many2one(
        "product.product", string="Artículo", required=True, readonly=True,
        index=True, ondelete="cascade",
    )
    id_articulo = fields.Char(string="Referencia enviada", readonly=True)
    location_id = fields.Many2one(
        "stock.location", string="Ubicación", required=True, readonly=True,
        index=True, ondelete="cascade",
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Almacén", readonly=True, ondelete="set null"
    )
    company_id = fields.Many2one(
        "res.company", string="Compañía", readonly=True,
        default=lambda self: self.env.company,
    )
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unidad")
    quantity = fields.Float(
        string="Unidades recibidas", readonly=True, digits="Product Unit"
    )
    counted_after = fields.Float(
        string="Contado tras el envío",
        readonly=True,
        digits="Product Unit",
        help="Cantidad contada del artículo después de sumar este envío.",
    )
    token_id = fields.Many2one(
        "matriz.almonte.api.token", string="PDA", readonly=True,
        ondelete="set null",
    )
    user_id = fields.Many2one("res.users", string="Usuario", readonly=True)
    pda_uuid = fields.Char(string="UUID del envío", readonly=True, index=True)
    payload_hash = fields.Char(readonly=True, index=True)
    pda_count_start = fields.Datetime(string="Inicio recuento PDA", readonly=True)
    pda_count_end = fields.Datetime(string="Envío recuento PDA", readonly=True)
    pda_count_comment = fields.Text(string="Comentario", readonly=True)
    create_date = fields.Datetime(string="Recibido", readonly=True)
