# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Etiquetas de producto Brother QL-700",
    "summary": (
        "Impresión de etiquetas de producto (referencia, nombre, precio con "
        "IVA incluido y código de barras) con el tamaño exacto para la "
        "impresora de etiquetas Brother QL-700 (5 x 2,9 cm)."
    ),
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["product", "account"],
    "data": [
        "security/ir.model.access.csv",
        "report/report_paperformat.xml",
        "report/product_label_report.xml",
        "wizard/product_label_wizard_views.xml",
        "views/product_template_views.xml",
        "views/product_product_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

