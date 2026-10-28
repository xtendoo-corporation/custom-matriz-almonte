from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

SEQUENCE_DIGITS = 6
# Espacio de nombres propio para los bloqueos consultivos de PostgreSQL.
ADVISORY_LOCK_NAMESPACE = 742519
SKIP_GENERATION_CONTEXT_KEY = "skip_category_default_code"


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if not self._skip_default_code_generation():
            products.filtered(lambda product: not product.default_code)._generate_default_code()
        return products

    @api.model
    def _skip_default_code_generation(self):
        # Los productos técnicos que cargan los módulos al instalarse no se numeran.
        context = self.env.context
        return context.get(SKIP_GENERATION_CONTEXT_KEY) or context.get("install_mode")

    def _generate_default_code(self):
        if not self:
            return
        missing_code = self.categ_id.filtered(lambda categ: not categ.product_reference_code)
        if missing_code:
            raise UserError(_(
                "No se puede generar la referencia del producto porque la categoría "
                "«%(categories)s» no tiene referencia. Asigna una referencia a la "
                "categoría o escribe la referencia del producto a mano.",
                categories="», «".join(missing_code.mapped("display_name")),
            ))
        for product in self:
            product.default_code = self._get_next_default_code(
                product.categ_id.product_reference_code
            )

    @api.model
    def _get_next_default_code(self, prefix):
        self._lock_default_code_prefix(prefix)
        self.flush_model(["default_code"])
        self.env.cr.execute(
            """
            SELECT MAX(default_code)
              FROM product_product
             WHERE default_code LIKE %s
               AND length(default_code) = %s
               AND default_code ~ '^[0-9]+$'
            """,
            (f"{prefix}%", len(prefix) + SEQUENCE_DIGITS),
        )
        last_code = self.env.cr.fetchone()[0]
        next_number = int(last_code[len(prefix):]) + 1 if last_code else 1
        if next_number >= 10 ** SEQUENCE_DIGITS:
            raise UserError(_(
                "Se ha agotado la numeración de referencias para el código %s.", prefix
            ))
        return f"{prefix}{next_number:0{SEQUENCE_DIGITS}d}"

    @api.model
    def _lock_default_code_prefix(self, prefix):
        # La longitud forma parte de la clave para no confundir "01" con "001".
        lock_key = len(prefix) * 1000 + int(prefix)
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (ADVISORY_LOCK_NAMESPACE, lock_key)
        )

    @api.constrains("default_code")
    def _check_default_code_unique(self):
        products = self.filtered("default_code")
        if not products:
            return
        duplicates = self.sudo().with_context(active_test=False).search([
            ("default_code", "in", products.mapped("default_code")),
            ("id", "not in", products.ids),
        ])
        codes = products.mapped("default_code")
        repeated_in_batch = {code for code in codes if codes.count(code) > 1}
        duplicated_codes = set(duplicates.mapped("default_code")) | repeated_in_batch
        if duplicated_codes:
            raise ValidationError(_(
                "Ya existe un producto con la referencia %s. Las referencias no "
                "pueden repetirse.",
                ", ".join(sorted(duplicated_codes)),
            ))
