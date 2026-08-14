# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Extensión de ``pos.config`` para almacenar el email de cierre de sesión."""

from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    session_closing_email = fields.Char(
        string="Email de cierre de sesión",
        help=(
            "Dirección de correo a la que se enviará automáticamente toda la "
            "información del cierre de cada sesión de este Punto de Venta "
            "(resumen de ventas, pagos, efectivo y el informe de detalle de "
            "ventas en PDF). Deja el campo vacío para no enviar ningún correo."
        ),
    )

