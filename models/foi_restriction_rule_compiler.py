# -*- coding: utf-8 -*-
import ast

from odoo import api, models


class FoiRestrictionRuleCompiler(models.AbstractModel):
    """Compiles `foi.restriction.policy` records into native `ir.rule`
    records.

    Each policy can contribute domain restrictions from two sources:
      1. The fixed business-area tabs (Menus, Products, Categories,
         Vendors, Customers, Journals, Warehouses, Locations) -- each with a
         baked-in whitelist/blacklist semantic, see _AREA_CONFIG below.
      2. The advanced Domain Rules tab (arbitrary allow/deny domains against
         any registered Target model) -- which may reference dynamic,
         per-request variables exactly like a native ir.rule domain does:
         `uid`, `user`, `time`, `datetime`, `relativedelta`, `context_today`.
         e.g. [('user_id', '=', uid)] for "only my own records".

    CRITICAL -- dynamic expressions are never evaluated at compile time.
    An earlier version of this compiler merged domain contributions by
    calling safe_eval() on each one, concatenating the resulting Python
    objects, then re-serializing the merged list. That collapses any
    reference to `uid`/`user`/`time` etc. into whatever value happened to
    be true for the ADMIN clicking Recompile -- not the end user the rule
    is actually evaluated for later. That would silently break the single
    most common restriction pattern in Odoo ("each user sees only their own
    records") and, worse, could bake in one specific user's id as a
    permanent literal for everyone.

    Instead, every contribution is built and merged as a list of raw,
    UNEVALUATED domain-term source strings (parsed only for shape via the
    `ast` module, never executed), so expressions like `uid` are carried
    through verbatim into the final ir.rule.domain_force string. Odoo's own
    security engine then evaluates that string fresh, per request, per
    actual end user -- exactly like every other native ir.rule.

    IMPORTANT -- audience grouping and merge semantics (unchanged from the
    original design): Odoo ORs together multiple ir.rule records that share
    the exact same `groups`, and ANDs together rules with different (or no)
    groups. Left naive, two contributions for the SAME audience on the SAME
    model would compile to separate ir.rule records that get OR'd --
    silently discarding whichever is more restrictive. So this compiler:

      1. Resolves each policy's full audience (its groups, plus a
         compiler-managed "carrier" group standing in for any named users,
         since ir.rule only understands groups).
      2. Collects every domain contribution -- from area tabs AND domain
         lines, across ALL policies -- bucketed by (model, audience), as
         lists of term strings.
      3. AND-merges every bucket's terms into a single ir.rule, so the most
         restrictive contribution always wins, never the most permissive.
      4. Leaves different audiences as separate ir.rule records, which
         Odoo's native engine ORs together -- matching the ordinary,
         expected Odoo behavior where an additional group can widen access.

    MENUS: ir.ui.menu is a normal model and its visibility genuinely
    respects ir.rule like any other model. The one difference is that menu
    visibility is computed through heavily cached methods for performance;
    recompile_all() clears that cache when any menu rule was compiled, so
    changes take effect without a server restart.

    Compilation is a deliberate, explicit step (call `recompile_all()` from
    a button or scheduled action), not triggered on every policy save, to
    avoid doing this bulk metadata work inline on every write.
    """

    _name = 'foi.restriction.rule.compiler'
    _description = 'Compiles restriction policies into native ir.rule records'

    _MANAGED_RULE_PREFIX = 'FOI Restriction: '
    _CARRIER_GROUP_PREFIX = 'FOI Restriction Audience (users): '

    # field_name -> (comodel, mode, hierarchical, rank_field)
    #   mode: 'whitelist' -> selected are the ONLY ones allowed; empty = no restriction
    #         'blacklist' -> selected are hidden; empty = no restriction
    #   hierarchical: expand selection to descendants via child_of
    #   rank_field: for models sharing res.partner between two tabs (Vendors
    #               vs Customers) -- the restriction only applies to records
    #               that ARE that kind of partner, leaving all other
    #               contacts untouched. See _build_area_terms.
    _AREA_CONFIG = {
        'menu_ids': ('ir.ui.menu', 'blacklist', False, None),
        'product_tmpl_ids': ('product.template', 'whitelist', False, None),
        'product_product_ids': ('product.product', 'whitelist', False, None),
        'product_categ_ids': ('product.category', 'whitelist', True, None),
        'vendor_ids': ('res.partner', 'whitelist', False, 'supplier_rank'),
        'customer_ids': ('res.partner', 'whitelist', False, 'customer_rank'),
        'journal_ids': ('account.journal', 'whitelist', False, None),
        'warehouse_ids': ('stock.warehouse', 'whitelist', False, None),
        'location_ids': ('stock.location', 'whitelist', True, None),
    }

    @api.model
    def recompile_all(self):
        """Recompute every ir.rule this module manages, from scratch."""
        self._remove_managed_rules()
        policies = self.env['foi.restriction.policy'].search([('active', '=', True)])

        # buckets: (model_name, frozenset(audience_group_ids)) -> {'audience_ids': set, 'terms': [str, ...], 'labels': set}
        buckets = {}
        menu_touched = False

        for policy in policies:
            audience_ids = self._resolve_audience_group_ids(policy)

            for field_name, (comodel, mode, hierarchical, rank_field) in self._AREA_CONFIG.items():
                terms = self._build_area_terms(policy, field_name, mode, hierarchical, rank_field)
                if not terms:
                    continue
                if comodel == 'ir.ui.menu':
                    menu_touched = True
                self._add_to_bucket(buckets, comodel, audience_ids, terms, policy.name)

            for line in policy.domain_line_ids:
                terms = self._build_domain_line_terms(line)
                if not terms:
                    continue
                if line.model_name == 'ir.ui.menu':
                    menu_touched = True
                self._add_to_bucket(
                    buckets, line.model_name, audience_ids, terms,
                    f"{policy.name} (domain rule)",
                )

        for (model_name, _audience_key), bucket in buckets.items():
            self._compile_bucket(model_name, bucket)

        if menu_touched:
            # See class docstring: menu visibility is cached for performance.
            self.env['ir.ui.menu'].clear_caches()

    def _add_to_bucket(self, buckets, model_name, audience_ids, terms, label):
        key = (model_name, frozenset(audience_ids))
        bucket = buckets.setdefault(key, {'audience_ids': audience_ids, 'terms': [], 'labels': set()})
        bucket['terms'].extend(terms)
        bucket['labels'].add(label)

    def _remove_managed_rules(self):
        # sudo() is deliberate here: writing ir.rule is an inherently
        # privileged operation. Gatekeeping for who can trigger compilation
        # happens at the menu / server action level (restricted to
        # group_restriction_manager), not via ir.rule's own ACL -- mirroring
        # how Odoo's own Settings > Technical > Record Rules requires
        # group_system rather than ad-hoc per-model ir.rule access.
        managed = self.env['ir.rule'].sudo().search([('name', 'like', self._MANAGED_RULE_PREFIX)])
        managed.unlink()

    def _resolve_audience_group_ids(self, policy):
        """Return the full set of res.groups ids this policy's audience maps
        to for native ir.rule purposes: its own groups, plus a carrier group
        standing in for any named users. Empty result means "everyone".
        """
        group_ids = set(policy.group_ids.ids)
        if policy.user_ids:
            carrier = self._get_or_create_carrier_group(policy)
            group_ids.add(carrier.id)
        return group_ids

    def _get_or_create_carrier_group(self, policy):
        """Get-or-create the technical, hidden group that carries this
        policy's 'Applies to Users' into a native ir.rule. Reused across
        recompiles rather than recreated every time, to avoid proliferation.
        """
        Group = self.env['res.groups'].sudo()
        carrier = policy.compiled_audience_group_id
        if carrier and carrier.exists():
            carrier.write({'users': [(6, 0, policy.user_ids.ids)]})
            return carrier
        carrier = Group.create({
            'name': f"{self._CARRIER_GROUP_PREFIX}{policy.name}",
            'users': [(6, 0, policy.user_ids.ids)],
        })
        policy.sudo().write({'compiled_audience_group_id': carrier.id})
        return carrier

    # -- Term extraction (never evaluates -- see class docstring) -----------

    def _domain_terms(self, domain_str):
        """Parse a domain string into its top-level term SOURCE STRINGS,
        without evaluating it, so dynamic references like `uid` are
        preserved verbatim rather than resolved now.
        """
        if not domain_str:
            return []
        tree = ast.parse(domain_str, mode='eval')
        if not isinstance(tree.body, ast.List):
            raise ValueError(
                "Domain must be a list literal, e.g. [('field', '=', value)]"
            )
        return [ast.get_source_segment(domain_str, elt) for elt in tree.body.elts]

    def _fully_connect_terms(self, terms):
        """Prefix enough '&' term-strings to make a multi-term list into one
        single, fully-connected expression -- needed before negating it,
        since '!' only negates the single next term.
        """
        if len(terms) <= 1:
            return terms
        return ["'&'"] * (len(terms) - 1) + terms

    def _negate_terms(self, terms):
        return ["'!'"] + self._fully_connect_terms(terms)

    def _build_area_terms(self, policy, field_name, mode, hierarchical, rank_field):
        """Return a list of domain-term source strings for one fixed-area
        field, or an empty list if that tab has nothing selected (i.e.
        contributes no restriction -- "empty = all allowed/visible").
        """
        ids = policy[field_name].ids
        if not ids:
            return []

        if mode == 'blacklist':
            return ["('id', 'not in', %r)" % (ids,)]

        # whitelist
        id_operator = 'child_of' if hierarchical else 'in'
        base_term = "('id', '%s', %r)" % (id_operator, ids)
        if rank_field:
            # Two tabs (Vendors/Customers) share res.partner. This
            # restriction must only apply to records that ARE that kind of
            # partner -- "not <rank_field> OR in the allowed set" -- so a
            # plain contact that is neither a vendor nor a customer is left
            # untouched by either tab, and a partner that is both must pass
            # BOTH tabs' whitelists once merged (see class docstring).
            return ["'|'", "('%s', '<=', 0)" % rank_field, base_term]
        return [base_term]

    def _build_domain_line_terms(self, line):
        try:
            terms = self._domain_terms(line.domain)
        except (SyntaxError, ValueError):
            # Should not happen if the save-time constraint did its job,
            # but never let a malformed line silently drop a restriction --
            # skip only this line rather than crash the whole recompile.
            return []
        if not terms:
            return []
        if line.mode == 'deny':
            return self._negate_terms(terms)
        return terms

    def _compile_bucket(self, model_name, bucket):
        ir_model = self.env['ir.model']._get(model_name)
        if not ir_model or not bucket['terms']:
            return
        domain_force = "[" + ", ".join(bucket['terms']) + "]"
        names = ", ".join(sorted(bucket['labels']))
        self.env['ir.rule'].sudo().create({
            'name': f"{self._MANAGED_RULE_PREFIX}{names}",
            'model_id': ir_model.id,
            'domain_force': domain_force,
            'groups': [(6, 0, list(bucket['audience_ids']))],
            'perm_read': True,
            'perm_write': True,
            'perm_create': True,
            'perm_unlink': True,
        })
