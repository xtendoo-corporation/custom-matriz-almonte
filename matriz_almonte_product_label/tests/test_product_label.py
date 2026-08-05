# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests del módulo matriz_almonte_product_label."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProductLabelBrotherQl700(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_21 = cls.env["account.tax"].create(
            {
                "name": "Test IVA 21% (matriz_almonte_product_label)",
                "amount": 21.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": False,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Producto de prueba etiqueta",
                "default_code": "TESTLABEL001",
                "list_price": 10.0,
                "taxes_id": [(6, 0, cls.tax_21.ids)],
            }
        )
        cls.product_no_tax = cls.env["product.product"].create(
            {
                "name": "Producto sin impuestos",
                "default_code": "TESTLABEL002",
                "list_price": 5.0,
                "taxes_id": [(6, 0, [])],
            }
        )
        cls.report_model = cls.env[
            "report.matriz_almonte_product_label.product_label_report"
        ]

    def test_price_with_tax(self):
        price = self.report_model._get_price_with_tax(self.product)
        self.assertAlmostEqual(price, 12.1, places=2)

    def test_price_without_tax(self):
        price = self.report_model._get_price_with_tax(self.product_no_tax)
        self.assertAlmostEqual(price, 5.0, places=2)

    def test_barcode_image(self):
        image = self.report_model._get_barcode_image(self.product.default_code)
        self.assertTrue(image)
        self.assertTrue(image.startswith("data:image/png;base64,"))

    def test_barcode_image_without_reference(self):
        self.assertFalse(self.report_model._get_barcode_image(False))

    def test_wizard_default_get_from_product_product(self):
        wizard_model = self.env["matriz_almonte_product_label_wizard"].with_context(
            active_model="product.product", active_ids=self.product.ids
        )
        vals = wizard_model.default_get(["product_ids", "custom_quantity"])
        wizard = wizard_model.create(vals)
        self.assertIn(self.product, wizard.product_ids)

    def test_wizard_default_get_from_product_template(self):
        wizard_model = self.env["matriz_almonte_product_label_wizard"].with_context(
            active_model="product.template",
            active_ids=self.product.product_tmpl_id.ids,
        )
        vals = wizard_model.default_get(["product_ids", "custom_quantity"])
        wizard = wizard_model.create(vals)
        self.assertIn(self.product, wizard.product_ids)

    def test_wizard_requires_product(self):
        wizard = self.env["matriz_almonte_product_label_wizard"].create(
            {"custom_quantity": 1}
        )
        with self.assertRaises(UserError):
            wizard.action_print_report()

    def test_wizard_invalid_quantity(self):
        with self.assertRaises(ValidationError):
            self.env["matriz_almonte_product_label_wizard"].create(
                {
                    "product_ids": [(6, 0, self.product.ids)],
                    "custom_quantity": 0,
                }
            )

    def test_report_values_quantity(self):
        values = self.report_model._get_report_values(
            self.product.ids,
            {"custom_quantity": 3, "product_ids": self.product.ids},
        )
        self.assertEqual(len(values["labels"]), 3)
        for label in values["labels"]:
            self.assertEqual(label["default_code"], "TESTLABEL001")
            self.assertIn("12", label["price"])

    def test_wizard_process_report_action(self):
        wizard = self.env["matriz_almonte_product_label_wizard"].create(
            {
                "product_ids": [(6, 0, self.product.ids)],
                "custom_quantity": 2,
            }
        )
        action = wizard.action_print_report()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(
            action["report_name"],
            "matriz_almonte_product_label.product_label_report",
        )




