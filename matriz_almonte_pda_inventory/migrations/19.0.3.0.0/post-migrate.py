# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""El número de inventario ya no lleva la PDA: «INV/00045 (PDA Olga)» → «INV/00045».

Ahora un inventario puede recibir envíos de varias PDA; la PDA de cada envío
queda en el registro de envíos.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE stock_quant
           SET pda_count_origin = split_part(pda_count_origin, ' (PDA ', 1)
         WHERE pda_count_origin LIKE '%% (PDA %%'
        """
    )
