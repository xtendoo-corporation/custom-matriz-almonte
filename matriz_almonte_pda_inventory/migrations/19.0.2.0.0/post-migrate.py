# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Elimina el registro propio de inventarios PDA de la versión 1.

Desde la 2.0 el recuento va directamente al Inventario físico estándar de
Odoo. Los movimientos de stock ya aplicados por la versión 1 no se tocan.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        "DELETE FROM ir_sequence WHERE code = %s",
        ["matriz.almonte.pda.inventory"],
    )
    cr.execute(
        """
        DROP TABLE IF EXISTS matriz_almonte_pda_inventory_line;
        DROP TABLE IF EXISTS matriz_almonte_pda_inventory;
        """
    )
    _logger.info("Eliminadas las tablas y la secuencia de inventarios PDA (v1).")
