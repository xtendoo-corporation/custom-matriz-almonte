# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Los inventarios nuevos se numeran INVENTORY/00001 en lugar de INV/00001.

La secuencia es noupdate, así que el prefijo se cambia aquí. Los números ya
emitidos (líneas pendientes, registro y movimientos) no se tocan.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_sequence
           SET prefix = 'INVENTORY/'
         WHERE code = 'matriz.almonte.pda.inventory.count'
           AND prefix = 'INV/'
        """
    )
