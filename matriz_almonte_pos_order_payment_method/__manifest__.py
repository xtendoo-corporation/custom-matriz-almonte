# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Forma de pago en la lista de pedidos POS",
    "summary": (
        "Añade una columna con la forma (o formas) de pago utilizada en cada "
        "pedido, tanto en la lista de pedidos del backend como en la pantalla "
        "de pedidos dentro del Punto de Venta."
    ),
    "version": "19.0.1.1.0",
    "category": "Sales/Point of Sale",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "views/pos_order_view.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "matriz_almonte_pos_order_payment_method/static/src/app/screens/ticket_screen/ticket_screen.js",
            "matriz_almonte_pos_order_payment_method/static/src/app/screens/ticket_screen/ticket_screen.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}

