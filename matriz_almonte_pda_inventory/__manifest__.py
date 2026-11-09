# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Inventario desde PDA",
    "summary": (
        "API PDA que registra el recuento físico de una tienda como cantidad "
        "contada en el Inventario físico de Odoo, pendiente de aplicar."
    ),
    "version": "19.0.2.2.0",
    "category": "Inventory/Inventory",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["stock", "matriz_almonte_pda_sale_import"],
    "data": ["data/ir_sequence.xml", "views/stock_quant_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
