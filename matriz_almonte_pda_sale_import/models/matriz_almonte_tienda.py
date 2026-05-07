from odoo import models, fields

class MatrizAlmonteTienda(models.Model):
    _name = 'matriz.almonte.tienda'
    _description = 'Tienda de Matriz Almonte'

    # Campo con la descripción/nombre de la tienda
    name = fields.Char(string='Nombre de la Tienda', required=True)