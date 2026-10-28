# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Almacén destino en compras",
    "summary": (
        "Indica en el pedido de compra a qué tienda va la mercancía y, al "
        "recibirla en central, crea el traspaso a esa tienda con lo recibido."
    ),
    "version": "19.0.1.0.0",
    "category": "Inventory/Purchase",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["purchase_stock"],
    "data": ["views/purchase_order_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
