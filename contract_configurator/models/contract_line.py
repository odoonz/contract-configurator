# Copyright 2022 Graeme Gellatly
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import _, api, fields, models
from odoo.tools import float_compare

from odoo.addons.sale_configurator_base.models.sale import update_attrs


class ContractContract(models.Model):
    _inherit = "contract.contract"

    main_line_ids = fields.One2many(
        "contract.line", "contract_id", domain=[("parent_id", "=", False)]
    )

    def sync_sequence(self):
        for record in self:
            done = []
            for line in record.contract_line_ids.sorted("sequence"):
                if not line.parent_id:
                    line.sequence = len(done)
                    done.append(line)
                    line._sort_children_line(done)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.sync_sequence()
        return records

    def write(self, vals):
        super().write(vals)
        if "contract_line_ids" in vals:
            self.sync_sequence()
        return True

    @api.onchange("contract_line_ids")
    def onchange_sale_line_sequence(self):
        self.sync_sequence()

    @api.model
    def _fields_view_get(
        self, view_id=None, view_type="form", toolbar=False, submenu=False
    ):
        """fields_view_get comes from Model (not AbstractModel)"""
        res = super()._fields_view_get(
            view_id=view_id,
            view_type=view_type,
            toolbar=toolbar,
            submenu=submenu,
        )
        if view_type == "form" and not self._context.get("force_original_contract_form"):
            doc = etree.XML(res["arch"])
            for field in doc.xpath("//field[@name='contract_line_ids']/tree/field"):
                fname = field.get("name")
                if fname != "sequence":
                    if not self.env["contract.line"]._fields[fname].readonly:
                        update_attrs(
                            field,
                            {
                                "readonly": [
                                    "|",
                                    ("parent_id", "!=", False),
                                    ("is_configurable", "=", True),
                                ]
                            },
                        )
                if fname == "product_id":
                    field.set(
                        "class", field.get("class", "") + " configurator_option_padding"
                    )
                if fname == "name":
                    field.set(
                        "class",
                        field.get("class", "")
                        + " description configurator_option_padding",
                    )
            res["arch"] = etree.tostring(doc, pretty_print=True).decode("utf-8")
        return res


class ContractLine(models.Model):

    _inherit = "contract.line"

    parent_id = fields.Many2one(
        "contract.line",
        "Parent Line",
        ondelete="cascade",
        index=True,
        compute="_compute_parent",
        store=True,
    )
    # Becarefull never use child_ids in computed field because odoo is going
    # to do crazy thing, indead inside you will have duplicated data
    # (with real id and with Newid) so please instead use get_children method
    # child_ids is used for reporting
    child_ids = fields.One2many("contract.line", "parent_id", "Children Lines")
    child_type = fields.Selection([("option", "Option")],
        ondelete={"option": "set null"}, compute="_compute_parent", store=True)
    price_config_subtotal = fields.Float(
        compute="_compute_config_amount",
        string="Config Subtotal",
        readonly=True,
        store=True,
    )
    # price_config_total = fields.Float(
    #     compute="_compute_config_amount",
    #     string="Config Total",
    #     readonly=True,
    #     store=True,
    # )
    pricelist_id = fields.Many2one(related="contract_id.pricelist_id", string="Pricelist")
    # There is already an order_partner_id in the sale line class
    # but we want to make the view as much compatible between child view
    # wo want a native view do parent.partner_id we want to have the same behaviour
    # with the child line (but in that case the parent is a sale order line
    partner_id = fields.Many2one(related="contract_id.partner_id", string="Customer")

    is_configurable = fields.Boolean(
        "Line is a configurable Product ?",
        compute="_compute_is_configurable",
    )
    report_line_is_empty_parent = fields.Boolean(
        compute="_compute_report_line_is_empty_parent",
        help="Technical field used in the report to hide subtotals"
        " and taxes in case a parent line (with children lines) "
        "has no price by itself",
    )
    quantity = fields.Float(
        compute="_compute_quantity",
        readonly=False,
        store=True,
    )

    # In different implementation the price unit can depend on other lines
    # So in the base module we add an empty generic implementation
    price_unit = fields.Float(
        compute="_compute_price_unit",
        readonly=False,
        store=True,
    )
    hide_subtotal = fields.Boolean(compute="_compute_hide_subtotal")

    parent_option_id = fields.Many2one("contract.line", string="Parent Option")

    option_ids = fields.One2many(
        "contract.line",
        "parent_option_id",
        "Options",
        copy=False,
    )
    is_configurable_opt = fields.Boolean(
        "Is the product configurable Option ?", related="product_id.is_configurable_opt"
    )
    option_unit_qty = fields.Float(
        string="Option Unit Qty",
        digits="Product Unit of Measure",
        default=1.0,
    )
    option_qty_type = fields.Selection(
        [
            ("proportional_qty", "Proportional Qty"),
            ("independent_qty", "Independent Qty"),
        ],
        string="Option qty Type",
        compute="_compute_option_qty_type",
        store=True,
        readonly=False,
    )
    product_option_id = fields.Many2one(
        "product.configurator.option",
        "Product Option",
        ondelete="set null",
        compute="_compute_product_option_id",
    )

    # TODO in V16 the price_unit is a computed field \o/
    # so we should be able to drop this
    @api.depends("quantity")
    def _compute_price_unit(self):
        for record in self:
            if record.child_type == "option":
                product = record.product_id.with_context(
                    partner=record.order_id.partner_id,
                    quantity=record.quantity,
                    date=record.order_id.date_order,
                    pricelist=record.order_id.pricelist_id.id,
                    uom=record.product_uom.id,
                )
                record.price_unit = record._get_display_price(product)

    @api.depends("parent_option_id")
    def _compute_parent(self):
        for record in self:
            if record.parent_option_id:
                record.parent_id = record.parent_option_id
                record.child_type = "option"
            else:
                record.parent_id = None
                record.child_type = None

    def _get_child_type_sort(self):
        return [(20, "option")]

    def _is_line_configurable(self):
        return self.is_configurable_opt

    @api.depends(
        "quantity",
        "option_unit_qty",
        "option_qty_type",
        "parent_option_id.quantity",
    )
    def _compute_quantity(self):
        for record in self:
            if record.parent_option_id:
                if record.option_qty_type == "proportional_qty":
                    record.quantity = (
                        record.option_unit_qty * record.parent_option_id.quantity
                    )
                elif record.option_qty_type == "independent_qty":
                    record.quantity = record.option_unit_qty

    @api.onchange("quantity")
    def onchange_qty_propagate_to_child(self):
        # When adding a new configurable product the qty is not propagated
        # correctly to child line with the onchange (it work when modifying)
        # seem to have a bug in odoo ORM
        for record in self:
            record.option_ids._compute_quantity()

    @api.model_create_multi
    def create(self, vals_list):
        options_list = [vals.pop("option_ids", None) for vals in vals_list]
        for vals in vals_list:
            parent_id = self._get_parent_id_from_vals(vals)
            if parent_id and "contract_id" not in vals:
                vals["contract_id"] = self.browse(parent_id).contract_id.id
        lines = super().create(vals_list)
        # For weird reason it seem that the quantity have been not recomputed
        # correctly. Recompute is only triggered in the onchange
        # and the onchange do not propagate the qty see the following test:
        # tests/test_sale_order.py::SaleOrderCase::test_create_sale_with_option_ids
        # Note maybe it's because the quantity have a default value
        # and so the create will add it, end then if we have a value the recompute
        # is note done
        lines._compute_quantity()

        # We ensure to write the option after all field on the main line a recomputed
        if any(options_list):
            for line, vals in zip(lines, options_list):
                if vals:
                    line.write({"option_ids": vals})

        return lines

    def _get_product_option(self):
        self.ensure_one()
        return self.parent_option_id.product_id.configurable_option_ids.filtered(
            lambda o: o.product_id == self.product_id
        )

    @api.depends("product_id")
    def _compute_product_option_id(self):
        for record in self:
            record.product_option_id = record._get_product_option()

    @api.depends("product_id")
    def _compute_option_qty_type(self):
        for record in self:
            if record.product_option_id:
                record.option_qty_type = record.product_option_id.option_qty_type

    @api.onchange("product_id")
    def product_id_change(self):
        res = super().product_id_change()
        # Note we use here the context because we only want to add the default option
        # in odoo backend when editing a SO
        # Other module can call the method product_id_change and we do not want
        # to have weird side effect
        if self.product_id.is_configurable_opt and self._context.get(
            "add_default_option"
        ):
            self.option_ids = False
            for opt in self.product_id.configurable_option_ids:
                if opt.is_default_option:
                    option = self.new(
                        {
                            "product_id": opt.product_id.id,
                            "parent_option_id": self.id,
                            "order_id": self.order_id.id,
                            "option_unit_qty": opt.option_qty_default
                        }
                    )
                    option.product_id_change()
                    self.option_ids |= option
        return res

    def _get_parent_id_from_vals(self, vals):
        return vals.get("parent_option_id", False)

    @api.depends("option_ids")
    def _compute_report_line_is_empty_parent(self):
        super()._compute_report_line_is_empty_parent()

    def get_children(self):
        return self.option_ids


    def _compute_hide_subtotal(self):
        for record in self:
            record.hide_subtotal = (
                record.child_ids
                and not record.price_unit
                or not record.parent_id
                and not record.child_ids
            )

    def _sort_children_line(self, done):
        types = self._get_child_type_sort()
        types.sort()
        for _position, child_type in types:
            for line in self.get_children().sorted("sequence"):
                if line.child_type == child_type:
                    line.sequence = len(done)
                    done.append(line)

    @api.depends("price_unit")
    def _compute_report_line_is_empty_parent(self):
        for rec in self:
            rec.report_line_is_empty_parent = False
            price_unit_like_zero = (
                float_compare(rec.price_unit, 0.00, precision_digits=2) == 0
            )
            if rec.get_children() and price_unit_like_zero:
                rec.report_line_is_empty_parent = True

    @api.depends("product_id")
    def _compute_is_configurable(self):
        for record in self:
            record.is_configurable = record._is_line_configurable()

    def save_add_product_and_close(self):
        return {"type": "ir.actions.act_window_close"}

    def save_add_product_and_new(self):
        return self.browse().open_contract_line_config_base()

    def open_contract_line_config_base(self):
        view_id = self.env.ref(
            "contract_configurator.contract_config_base_view_form"
        ).id
        return {
            "name": _("Base Configurator"),
            "type": "ir.actions.act_window",
            "context": self._context,
            "view_mode": "form",
            "res_model": self._name,
            "view_id": view_id,
            "views": [(view_id, "form")],
            "target": "new",
            "res_id": self.id,
        }

    @api.depends(
        "price_subtotal",
        "parent_id",
        "option_ids.price_subtotal"
    )
    def _compute_config_amount(self):
        """
        Compute the config amounts of the SO line.
        """
        for line in self:
            line.update(line._get_price_config())

    def _get_price_config(self):
        self.ensure_one()
        if self.parent_id:
            return {
                "price_config_subtotal": 0,
            }
        else:
            return {
                "price_config_subtotal": self.price_subtotal
                + sum(self.get_children().mapped("price_subtotal")),
            }

    def write(self, vals):
        super().write(vals)
        if "option_ids" in vals:
            self.contract_id.sync_sequence()
        return True
