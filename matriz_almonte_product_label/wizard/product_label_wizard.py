# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MatrizAlmonteProductLabelWizard(models.TransientModel):
    _name = "matriz_almonte_product_label_wizard"
    _description = "Imprimir etiquetas de producto (Brother QL-700)"

    product_ids = fields.Many2many(
        comodel_name="product.product",
        string="Productos",
        required=True,
    )
    custom_quantity = fields.Integer(
        string="Etiquetas por producto",
        default=1,
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids")
        if active_model and active_ids:
            products = self.env["product.product"]
            if active_model == "product.template":
                templates = self.env["product.template"].browse(active_ids)
                products = templates.product_variant_ids
            elif active_model == "product.product":
                products = self.env["product.product"].browse(active_ids)
            if products:
                res["product_ids"] = [(6, 0, products.ids)]
        return res

    @api.constrains("custom_quantity")
    def _check_custom_quantity(self):
        for wizard in self:
            if wizard.custom_quantity < 1:
                raise ValidationError(
                    _("El número de etiquetas por producto debe ser al menos 1.")
                )

    def action_print_report(self):
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_("Debe seleccionar al menos un producto."))
        data = {
            "custom_quantity": self.custom_quantity,
            "product_ids": self.product_ids.ids,
        }
        report = self.env.ref(
            "matriz_almonte_product_label.action_report_product_label_brother_ql700"
        )
        return report.report_action(self.product_ids.ids, data=data)



