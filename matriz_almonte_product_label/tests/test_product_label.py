# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests del módulo matriz_almonte_product_label."""

from markupsafe import Markup

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

    def test_format_price_fixed_euro_symbol(self):
        """El símbolo de moneda debe ser SIEMPRE "€", fijo, sin depender de
        ``product.currency_id`` (que podría no mostrar el símbolo).

        Se comprueba la entidad HTML numérica del euro (``&#8364;``), no
        el carácter Unicode literal: el documento HTML final pierde la
        declaración de charset UTF-8 (ver comentario en la plantilla QWeb),
        lo que corrompía el carácter "€" directo. Además debe ser
        ``Markup`` (HTML seguro) para que QWeb no escape el "&" de la
        entidad al pintarlo con ``t-out``.
        """
        price = self.report_model._format_price(12.1)
        self.assertIsInstance(price, Markup)
        self.assertEqual(price, "12,10 &#8364;")
        self.assertEqual(self.report_model._format_price(5), "5,00 &#8364;")
        self.assertEqual(self.report_model._format_price(1234.5), "1234,50 &#8364;")

    def test_barcode_image(self):
        image = self.report_model._get_barcode_image(self.product.default_code)
        self.assertTrue(image)
        self.assertTrue(image["src"].startswith("data:image/png;base64,"))
        self.assertGreater(image["width"], 0)
        self.assertGreater(image["height"], 0)

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
            self.assertEqual(label["price"], "12,10 &#8364;")

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

    def test_render_qweb_pdf(self):
        """Test de regresión: el informe debe renderizarse sin errores.

        Cubre dos bugs detectados manualmente:
        - IndexError ("//main" no encontrado) si la plantilla no envuelve
          el contenido en un <main> (falla en ``_prepare_html``, antes de
          invocar wkhtmltopdf, así que se detecta también en modo test).
        - Páginas duplicadas/vacías si el layout usa flexbox en lugar de
          posicionamiento absoluto (bug de wkhtmltopdf con páginas tan
          pequeñas; verificado manualmente generando el PDF real, ya que
          en los tests Odoo sustituye wkhtmltopdf por HTML para no
          ralentizar la suite).
        """
        report = self.env.ref(
            "matriz_almonte_product_label.action_report_product_label_brother_ql700"
        )
        data = {"custom_quantity": 3, "product_ids": self.product.ids}
        content, report_type = report._render_qweb_pdf(
            "matriz_almonte_product_label.product_label_report",
            self.product.ids,
            data=data,
        )
        self.assertIn(report_type, ("pdf", "html"))
        self.assertTrue(content)
        content_str = (
            content if isinstance(content, str) else content.decode("utf-8")
        )
        self.assertEqual(content_str.count(self.product.default_code), 3)

    def test_style_is_inside_main(self):
        """Test de regresión CRÍTICO: el <style> debe estar DENTRO de <main>.

        ``ir_actions_report._prepare_html`` (Odoo 19), al no existir ningún
        ``div.article`` en la plantilla, aplica un fallback que SOLO
        conserva los hijos de <main> al reinsertar el contenido dentro del
        layout final (``web.minimal_layout``): todo lo que esté fuera de
        <main> (p.ej. un <style> en <head>) se descarta SILENCIOSAMENTE.

        Si esto ocurre, el CSS deja de aplicarse por completo y todo el
        posicionamiento absoluto de la etiqueta se rompe: el contenido cae
        en flujo normal y el nombre/precio/código de barras/referencia
        aparecen desplazados y amontonados en la parte superior de la
        etiqueta (bug detectado y corregido manualmente, verificado
        renderizando el PDF real y rasterizándolo).
        """
        values = self.report_model._get_report_values(
            self.product.ids,
            {"custom_quantity": 1, "product_ids": self.product.ids},
        )
        html = self.env["ir.qweb"]._render(
            "matriz_almonte_product_label.product_label_report", values
        )
        html = str(html)
        main_start = html.index("<main")
        main_end = html.index("</main>")
        style_start = html.index("<style")
        self.assertGreater(
            style_start,
            main_start,
            "El <style> debe estar DENTRO de <main>, si no Odoo lo descarta"
            " al reconstruir el documento final y se pierde todo el CSS.",
        )
        self.assertLess(
            style_start,
            main_end,
            "El <style> debe estar DENTRO de <main> (antes de </main>).",
        )




