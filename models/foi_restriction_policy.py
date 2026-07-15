# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FoiRestrictionPolicy(models.Model):
    """A restriction assignment: pick an audience (groups/users) once, then
    configure what that audience can see across a set of fixed business
    areas (tabs), plus one generic advanced tab for anything not covered.

    Semantics per fixed area:
      * Menus            -> BLACKLIST. Selected menus are hidden. Empty = all visible.
      * Everything else
        (Products, Categories, Vendors, Customers, Journals,
        Warehouses, Locations) -> WHITELIST. If anything is selected, ONLY
        those records are allowed. Empty = all allowed.

    The Domain Rules tab covers anything the fixed areas don't: an arbitrary
    allow/deny domain against any model, picked directly -- no separate
    registration step, e.g. "only my own records":
    [('user_id', '=', uid)].

    This record does not enforce anything by itself. `foi.restriction.rule
    .compiler` compiles the audience + fixed-area + domain-line contributions
    into native `ir.rule` records, which Odoo's own security engine then
    applies everywhere (UI, search, RPC, reports, import/export).
    """

    _name = 'foi.restriction.policy'
    _description = 'Restriction Policy'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name, id'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    group_ids = fields.Many2many(
        'res.groups', 'foi_restriction_policy_group_rel', 'policy_id', 'group_id',
        string='Applies to Groups', tracking=True,
        help="Audience for this policy. Combined with 'Applies to Users' as a "
             "UNION: applies to a user who matches EITHER a listed group OR "
             "a listed user. Leave both empty for everyone.",
    )
    user_ids = fields.Many2many(
        'res.users', 'foi_restriction_policy_user_rel', 'policy_id', 'user_id',
        string='Applies to Users', tracking=True,
        help="Named users this policy applies to, in addition to (not "
             "narrowing) any groups above. Leave both empty for everyone.",
    )

    # -- Fixed business-area tabs -------------------------------------------
    # Menus: BLACKLIST. Everything else below: WHITELIST. See class docstring.

    menu_ids = fields.Many2many(
        'ir.ui.menu', 'foi_restriction_policy_menu_rel', 'policy_id', 'menu_id',
        string='Hidden Menus',
        help="Selected menu items are HIDDEN for this audience. Leave empty "
             "to leave every menu visible (subject to Odoo's own native "
             "per-menu group visibility, which this does not replace).",
    )
    product_tmpl_ids = fields.Many2many(
        'product.template', 'foi_restriction_policy_product_tmpl_rel', 'policy_id', 'product_tmpl_id',
        string='Allowed Products',
        help="If any products are selected, ONLY those are allowed for this "
             "audience. Leave empty to allow all products.",
    )
    product_product_ids = fields.Many2many(
        'product.product', 'foi_restriction_policy_product_product_rel', 'policy_id', 'product_product_id',
        string='Allowed Product Variants',
        help="Optional finer-grained restriction at the variant level (e.g. "
             "one specific color/size). Independent of Allowed Products "
             "above -- leave empty to allow all variants.",
    )
    product_categ_ids = fields.Many2many(
        'product.category', 'foi_restriction_policy_product_categ_rel', 'policy_id', 'product_categ_id',
        string='Allowed Product Categories',
        help="If any categories are selected, ONLY those (and everything "
             "underneath them) are allowed. Leave empty to allow all categories.",
    )
    vendor_ids = fields.Many2many(
        'res.partner', 'foi_restriction_policy_vendor_rel', 'policy_id', 'partner_id',
        string='Allowed Vendors', domain=[('supplier_rank', '>', 0)],
        help="If any vendors are selected, ONLY those are allowed for this "
             "audience. Leave empty to allow all vendors. Does not affect "
             "non-vendor contacts.",
    )
    customer_ids = fields.Many2many(
        'res.partner', 'foi_restriction_policy_customer_rel', 'policy_id', 'partner_id',
        string='Allowed Customers', domain=[('customer_rank', '>', 0)],
        help="If any customers are selected, ONLY those are allowed for this "
             "audience. Leave empty to allow all customers. Does not affect "
             "non-customer contacts.",
    )
    journal_ids = fields.Many2many(
        'account.journal', 'foi_restriction_policy_journal_rel', 'policy_id', 'journal_id',
        string='Allowed Journals',
        help="If any journals are selected, ONLY those are allowed. Leave "
             "empty to allow all journals.",
    )
    warehouse_ids = fields.Many2many(
        'stock.warehouse', 'foi_restriction_policy_warehouse_rel', 'policy_id', 'warehouse_id',
        string='Allowed Warehouses',
        help="If any warehouses are selected, ONLY those are allowed. Leave "
             "empty to allow all warehouses.",
    )
    location_ids = fields.Many2many(
        'stock.location', 'foi_restriction_policy_location_rel', 'policy_id', 'location_id',
        string='Allowed Locations',
        help="If any locations are selected, ONLY those (and everything "
             "underneath them) are allowed. Leave empty to allow all locations.",
    )

    # -- Advanced tab ---------------------------------------------------------

    domain_line_ids = fields.One2many(
        'foi.restriction.policy.domain.line', 'policy_id', string='Domain Rules (Advanced)',
        help="For anything the fixed tabs above don't cover: an arbitrary "
             "allow/deny domain against any model.",
    )

    compiled_audience_group_id = fields.Many2one(
        'res.groups', readonly=True, copy=False, groups='base.group_no_one',
        help="Technical, compiler-managed group used to carry this policy's "
             "'Applies to Users' into a native ir.rule (which only "
             "understands groups). Do not edit manually.",
    )

    def _filter_applicable_to_current_user(self):
        """Narrow this recordset to policies whose audience matches the
        current user. Audience = union of group_ids and user_ids: a policy
        applies if the user is in ANY listed group OR IS a listed user.
        Both empty means "everyone".
        """
        user = self.env.user
        user_groups = user.groups_id
        return self.filtered(
            lambda p: (not p.group_ids and not p.user_ids)
            or bool(p.group_ids & user_groups)
            or (user in p.user_ids)
        )
