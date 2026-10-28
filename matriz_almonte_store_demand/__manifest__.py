# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Pre-pedidos de tienda",
    "summary": (
        "Las tiendas envían pre-pedidos a central; central consolida la "
        "demanda en un pedido único por proveedor y reparte la mercancía "
        "a cada tienda con traspasos desde el almacén central."
    ),
    "version": "19.0.1.0.0",
    "category": "Inventory/Purchase",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["mail", "purchase_stock", "sale_stock"],
    "data": [
        "security/store_demand_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "wizards/store_demand_consolidate_views.xml",
        "views/store_demand_views.xml",
        "views/store_demand_line_views.xml",
        "views/stock_warehouse_views.xml",
        "views/purchase_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
