# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Referencia de producto por categoría",
    "summary": (
        "Genera la referencia interna de los productos nuevos a partir del "
        "código numérico de su categoría más un correlativo de 6 dígitos, "
        "e impide referencias duplicadas."
    ),
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["product"],
    "data": [
        "views/product_category_views.xml",
        "views/product_template_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
