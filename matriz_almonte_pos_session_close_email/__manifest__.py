# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - Email de cierre de sesión POS",
    "summary": (
        "Añade un email en la configuración del Punto de Venta y envía a esa "
        "dirección toda la información del cierre de la sesión (resumen de "
        "ventas, pagos y efectivo) junto con el informe de detalle de ventas."
    ),
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "views/pos_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

