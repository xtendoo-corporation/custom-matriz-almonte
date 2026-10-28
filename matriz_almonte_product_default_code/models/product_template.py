from odoo import api, models

from .product_product import SKIP_GENERATION_CONTEXT_KEY


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        # La referencia escrita en la plantilla llega a la variante después de
        # crearla, así que se genera al final y solo para las que sigan vacías.
        templates = super(
            ProductTemplate, self.with_context(**{SKIP_GENERATION_CONTEXT_KEY: True})
        ).create(vals_list)
        templates = templates.with_env(self.env)
        products = self.env["product.product"]
        if not products._skip_default_code_generation():
            templates.product_variant_ids.filtered(
                lambda product: not product.default_code
            )._generate_default_code()
        return templates
