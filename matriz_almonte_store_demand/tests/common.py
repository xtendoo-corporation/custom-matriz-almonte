from odoo.fields import Command
from odoo.tests import TransactionCase, new_test_user


class StoreDemandCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.central = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.central.is_store_demand_central = True
        cls.shop_a = cls.env["stock.warehouse"].create({
            "name": "Tienda A", "code": "TDA", "company_id": cls.company.id,
        })
        cls.shop_b = cls.env["stock.warehouse"].create({
            "name": "Tienda B", "code": "TDB", "company_id": cls.company.id,
        })
        cls.vendor_1 = cls.env["res.partner"].create({"name": "Proveedor Uno"})
        cls.vendor_2 = cls.env["res.partner"].create({"name": "Proveedor Dos"})
        cls.product_1 = cls._create_product("Vela", cls.vendor_1, 2.0)
        cls.product_2 = cls._create_product("Medalla", cls.vendor_1, 5.0)
        cls.product_3 = cls._create_product("Estampa", cls.vendor_2, 1.0)
        cls.product_no_vendor = cls._create_product("Rosario", None, 0.0)

        user_group = "matriz_almonte_store_demand.group_store_demand_user"
        manager_group = "matriz_almonte_store_demand.group_store_demand_manager"
        cls.user_shop_a = new_test_user(
            cls.env, login="shop_a", groups=f"base.group_user,{user_group}"
        )
        cls.user_shop_b = new_test_user(
            cls.env, login="shop_b", groups=f"base.group_user,{user_group}"
        )
        cls.user_central = new_test_user(
            cls.env, login="central", groups=f"base.group_user,{manager_group}"
        )
        cls.user_shop_a.property_warehouse_id = cls.shop_a
        cls.user_shop_b.property_warehouse_id = cls.shop_b
        cls.user_central.property_warehouse_id = cls.central

    @classmethod
    def _create_product(cls, name, vendor, price):
        vals = {
            "name": name,
            "type": "consu",
            "is_storable": True,
            "purchase_ok": True,
        }
        if vendor:
            vals["seller_ids"] = [Command.create({
                "partner_id": vendor.id, "price": price,
            })]
        return cls.env["product.product"].create(vals)

    def _create_demand(self, user, warehouse, lines, send=False):
        demand = self.env["matriz.store.demand"].with_user(user).create({
            "warehouse_id": warehouse.id,
            "line_ids": [
                Command.create({"product_id": product.id, "product_qty": qty})
                for product, qty in lines
            ],
        })
        if send:
            demand.action_send()
        return demand

    def _set_central_stock(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(
            product, self.central.lot_stock_id, qty
        )

    def _open_wizard(self, demand_lines=None):
        model = self.env["matriz.store.demand.consolidate"].with_user(self.user_central)
        action = model.action_open_for_lines(demand_lines)
        return model.browse(action["res_id"])
