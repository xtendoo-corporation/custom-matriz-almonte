# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Inventario desde PDA",
    "summary": (
        "API PDA para recibir el recuento físico de una tienda y aplicar el "
        "inventario completo en el almacén del token."
    ),
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["stock", "matriz_almonte_pda_sale_import"],
    "data": [
        "security/ir.model.access.csv",
        "data/sequences.xml",
        "views/matriz_almonte_pda_inventory_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
