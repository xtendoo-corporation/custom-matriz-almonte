# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Campos relacionados para configurar la impresora de tickets de la PDA
desde Ajustes > Punto de Venta."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_pda_ticket_printer_host = fields.Char(
        related="pos_config_id.pda_ticket_printer_host",
        readonly=False,
        string="IP impresora ticket PDA",
    )
    pos_pda_ticket_printer_port = fields.Integer(
        related="pos_config_id.pda_ticket_printer_port",
        readonly=False,
        string="Puerto impresora ticket PDA",
    )
    pos_pda_ticket_printer_width_dots = fields.Integer(
        related="pos_config_id.pda_ticket_printer_width_dots",
        readonly=False,
        string="Ancho impresora (dots)",
    )


    pda_defer_invoice = fields.Boolean(
        string="Facturación diferida PDA",
        config_parameter="matriz_almonte_pda_sale_import.defer_invoice",
        help="Si está activo, el pedido y sus pagos se confirman primero y la "
        "factura simplificada/albarán se generan después (en el acto si hay "
        "que imprimir; si no, por un cron con reintentos).",
    )
