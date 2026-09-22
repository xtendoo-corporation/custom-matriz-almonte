# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Diario de Ventas POS (Excel)",
    "summary": (
        "Resumen de ventas del Punto de Venta exportable a Excel (.xlsx), "
        "totalizado por punto de venta y día, cruzando tipo de IVA y "
        "forma de pago (con sus totales por fila y columna), filtrando "
        "por rango de fechas y por uno, varios o todos los puntos de "
        "venta."
    ),
    "version": "19.0.3.0.0",
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
