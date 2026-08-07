# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Matriz Almonte - PDA Sale Import",
    "summary": (
        "Recepción, validación y almacenamiento de operaciones de venta "
        "enviadas desde PDAs Android mediante API JSON protegida por token."
    ),
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Xtendoo",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["base", "mail", "point_of_sale"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/sequences.xml",
        "data/res_partner_data.xml",
        "views/matriz_almonte_api_token_views.xml",
        "views/matriz_almonte_pda_sale_import_views.xml",
        "views/pos_order_views.xml",
        "views/pos_config_settings_views.xml",
        "views/menuitems.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
