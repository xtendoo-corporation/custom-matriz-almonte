# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Pedidos a proveedor desde PDA",
    "summary": (
        "API PDA para que las tiendas creen solicitudes de presupuesto a un "
        "proveedor con la tienda como almacén destino."
    ),
    "version": "19.0.2.0.0",
    "category": "Inventory/Purchase",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": [
        "matriz_almonte_pda_sale_import",
        "matriz_almonte_purchase_destination",
    ],
    "data": ["views/purchase_order_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
