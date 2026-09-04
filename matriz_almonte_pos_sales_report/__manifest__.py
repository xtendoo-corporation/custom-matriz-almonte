# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Diario de Ventas POS (Excel)",
    "summary": (
        "Diario de ventas del Punto de Venta exportable a Excel (.xlsx), "
        "con una línea por cada forma de pago y tipo de IVA de cada "
        "ticket, filtrando por rango de fechas y por uno, varios o "
        "todos los puntos de venta."
    ),
    "version": "19.0.2.0.0",
    "category": "Sales/Point of Sale",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "report_xlsx"],
    "data": [
        "security/ir.model.access.csv",
        "report/pos_sales_report_xlsx.xml",
        "wizard/pos_sales_report_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
