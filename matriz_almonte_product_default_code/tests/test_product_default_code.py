from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProductDefaultCode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category_model = cls.env["product.category"]
        cls.categ_candles = category_model.create({
            "name": "Velas", "product_reference_code": "01",
        })
        cls.categ_candles_bis = category_model.create({
            "name": "Velas especiales", "product_reference_code": "01",
        })
        cls.categ_three_digits = category_model.create({
            "name": "Rosarios", "product_reference_code": "010",
        })
        cls.categ_without_code = category_model.create({"name": "Sin código"})

    def _create_template(self, category, **vals):
        return self.env["product.template"].create(
            {"name": "Producto", "categ_id": category.id, **vals}
        )

    def test_category_reference_must_be_two_or_three_digits(self):
        for invalid_code in ("1", "1234", "A1", "0 1", "1.5"):
            with self.subTest(code=invalid_code), self.assertRaises(ValidationError):
                self.categ_candles.product_reference_code = invalid_code
        self.categ_candles.product_reference_code = "123"
        self.categ_candles.product_reference_code = False

    def test_category_reference_is_not_unique(self):
        self.assertEqual(
            self.categ_candles.product_reference_code,
            self.categ_candles_bis.product_reference_code,
        )

    def test_first_product_of_category_starts_at_one(self):
        template = self._create_template(self.categ_candles)
        self.assertEqual(template.default_code, "01000001")
        self.assertEqual(template.product_variant_id.default_code, "01000001")

    def test_next_code_continues_from_highest_existing(self):
        self._create_template(self.categ_candles, default_code="01000002")
        self._create_template(self.categ_candles, default_code="0100000X")
        self._create_template(self.categ_candles, default_code="9901000999")
        template = self._create_template(self.categ_candles)
        self.assertEqual(template.default_code, "01000003")

    def test_categories_with_same_code_share_numbering(self):
        first = self._create_template(self.categ_candles)
        second = self._create_template(self.categ_candles_bis)
        self.assertEqual(first.default_code, "01000001")
        self.assertEqual(second.default_code, "01000002")

    def test_two_and_three_digit_codes_do_not_mix(self):
        self._create_template(self.categ_candles, default_code="01000050")
        template = self._create_template(self.categ_three_digits)
        self.assertEqual(template.default_code, "010000001")
        self.assertEqual(self._create_template(self.categ_candles).default_code, "01000051")

    def test_archived_products_count_for_numbering(self):
        self._create_template(self.categ_candles, default_code="01000007", active=False)
        self.assertEqual(self._create_template(self.categ_candles).default_code, "01000008")

    def test_batch_creation_gets_consecutive_codes(self):
        templates = self.env["product.template"].create([
            {"name": "Vela 1", "categ_id": self.categ_candles.id},
            {"name": "Vela 2", "categ_id": self.categ_candles.id},
            {"name": "Rosario", "categ_id": self.categ_three_digits.id},
        ])
        self.assertEqual(
            templates.mapped("default_code"), ["01000001", "01000002", "010000001"]
        )

    def test_manual_reference_is_respected(self):
        template = self._create_template(self.categ_candles, default_code="MANUAL-1")
        self.assertEqual(template.default_code, "MANUAL-1")
        self.assertEqual(self._create_template(self.categ_candles).default_code, "01000001")

    def test_manual_reference_allowed_in_category_without_code(self):
        template = self._create_template(self.categ_without_code, default_code="LIBRE")
        self.assertEqual(template.default_code, "LIBRE")

    def test_category_without_code_blocks_creation(self):
        with self.assertRaisesRegex(UserError, "Sin código"):
            self._create_template(self.categ_without_code)
        with self.assertRaisesRegex(UserError, "Sin código"):
            self.env["product.product"].create({
                "name": "Variante", "categ_id": self.categ_without_code.id,
            })

    def test_direct_variant_creation_generates_code(self):
        product = self.env["product.product"].create({
            "name": "Vela directa", "categ_id": self.categ_candles.id,
        })
        self.assertEqual(product.default_code, "01000001")

    def test_form_creation_generates_code(self):
        with Form(self.env["product.template"]) as template_form:
            template_form.name = "Vela de formulario"
            template_form.categ_id = self.categ_candles
        self.assertEqual(template_form.record.default_code, "01000001")

    def test_each_variant_gets_its_own_code(self):
        attribute = self.env["product.attribute"].create({
            "name": "Tamaño",
            "value_ids": [Command.create({"name": "S"}), Command.create({"name": "L"})],
        })
        template = self._create_template(
            self.categ_candles,
            attribute_line_ids=[Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(attribute.value_ids.ids)],
            })],
        )
        self.assertEqual(
            sorted(template.product_variant_ids.mapped("default_code")),
            ["01000001", "01000002"],
        )

    def test_changing_category_keeps_reference(self):
        template = self._create_template(self.categ_candles)
        template.categ_id = self.categ_three_digits
        self.assertEqual(template.default_code, "01000001")

    def test_copy_generates_new_reference(self):
        template = self._create_template(self.categ_candles)
        self.assertEqual(template.copy().default_code, "01000002")
        self.assertEqual(template.product_variant_id.copy().default_code, "01000003")

    def test_duplicate_reference_is_rejected(self):
        template = self._create_template(self.categ_candles)
        with self.assertRaisesRegex(ValidationError, "01000001"):
            self._create_template(self.categ_without_code, default_code="01000001")
        other = self._create_template(self.categ_candles)
        with self.assertRaises(ValidationError):
            other.default_code = template.default_code
        template.active = False
        with self.assertRaises(ValidationError):
            self._create_template(self.categ_without_code, default_code="01000001")

    def test_duplicate_reference_in_same_batch_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["product.product"].create([
                {"name": "A", "default_code": "REP"},
                {"name": "B", "default_code": "REP"},
            ])

    def test_existing_duplicates_only_fail_when_reference_changes(self):
        product_model = self.env["product.product"].with_context(
            skip_category_default_code=True
        )
        first = product_model.create({"name": "Antiguo 1"})
        second = product_model.create({"name": "Antiguo 2"})
        self.env.cr.execute(
            "UPDATE product_product SET default_code = 'OLD' WHERE id IN %s",
            (tuple((first | second).ids),),
        )
        self.env.invalidate_all()
        second.name = "Antiguo 2 renombrado"
        with self.assertRaises(ValidationError):
            second.default_code = "OLD"

    def test_module_data_loading_skips_generation(self):
        template = self.env["product.template"].with_context(install_mode=True).create({
            "name": "Producto técnico", "categ_id": self.categ_without_code.id,
        })
        self.assertFalse(template.default_code)

    def test_numbering_exhausted(self):
        self._create_template(self.categ_candles, default_code="01999999")
        with self.assertRaisesRegex(UserError, "agotado"):
            self._create_template(self.categ_candles)
