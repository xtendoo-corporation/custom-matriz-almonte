# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Análisis de pedidos TPV por método de pago e IVA",
    "summary": (
        "Amplía el análisis de pedidos del Punto de Venta para agrupar a la "
        "vez por método de pago y por tipo de IVA, repartiendo los importes "
        "de cada línea entre los pagos del pedido sin duplicar totales."
    ),
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Xtendoo Software SLU",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "views/pos_order_report_views.xml",
        "data/ir_filters_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
