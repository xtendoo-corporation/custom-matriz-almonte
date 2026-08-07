# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Extensión de ``pos.config`` con la configuración de la impresora térmica.

Permite imprimir directamente (sin bridge intermedio) el ticket de los
pedidos importados desde la PDA en una impresora térmica de 80 mm
conectada por red (ESC/POS sobre TCP, puerto 9100 por defecto).
"""

from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    pda_ticket_printer_host = fields.Char(
        string="IP impresora ticket PDA",
        help=(
            "Dirección IP o nombre de host de la impresora térmica de 80 mm "
            "para imprimir directamente el ticket de los pedidos que llegan "
            "desde la PDA (impresión RAW/ESC-POS por TCP). Si se deja vacío se "
            "usará el bridge local de impresión si está configurado."
        ),
    )
    pda_ticket_printer_port = fields.Integer(
        string="Puerto impresora ticket PDA",
        default=9100,
        help=(
            "Puerto TCP de la impresora térmica (RAW/JetDirect). "
            "El valor estándar es 9100."
        ),
    )

