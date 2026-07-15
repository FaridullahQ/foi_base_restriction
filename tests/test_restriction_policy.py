# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestFoiRestrictionPolicyValidation(TransactionCase):
    """Tests for policy-level and domain-line validation."""

    def setUp(self):
        super().setUp()
        self.partner_model = self.env['ir.model']._get('res.partner')

    def test_domain_line_invalid_syntax_raises(self):
        policy = self.env['foi.restriction.policy'].create({'name': 'Test Policy'})
        with self.assertRaises(ValidationError):
            self.env['foi.restriction.policy.domain.line'].create({
                'policy_id': policy.id,
                'target_id': self.partner_model.id,
                'mode': 'deny',
                'domain': 'not a valid domain(',
            })

    def test_domain_line_valid_syntax_is_accepted(self):
        policy = self.env['foi.restriction.policy'].create({'name': 'Test Policy'})
        line = self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': self.partner_model.id,
            'mode': 'deny',
            'domain': "[('company_id', '!=', False)]",
        })
        self.assertTrue(line.exists())

    def test_domain_line_syntax_validation_accepts_uid_and_user(self):
        """The save-time syntax check must not reject the most common
        dynamic patterns."""
        policy = self.env['foi.restriction.policy'].create({'name': 'Dynamic Domain Policy'})
        for valid_domain in [
            "[('user_id', '=', uid)]",
            "[('user_id', '=', user.id)]",
            "[('create_date', '>=', context_today())]",
        ]:
            line = self.env['foi.restriction.policy.domain.line'].create({
                'policy_id': policy.id,
                'target_id': self.partner_model.id,
                'mode': 'allow',
                'domain': valid_domain,
            })
            self.assertTrue(line.exists())
            line.unlink()

    def test_domain_line_target_picks_any_model_directly(self):
        """No registration step required -- any installed, non-transient
        model can be picked directly as the target."""
        sale_order_model = self.env['ir.model']._get('sale.order')
        policy = self.env['foi.restriction.policy'].create({'name': 'Test Policy'})
        line = self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': sale_order_model.id,
            'mode': 'allow',
            'domain': "[('user_id', '=', uid)]",
        })
        self.assertEqual(line.model_name, 'sale.order')

    def test_audience_union_semantics(self):
        """A policy naming both a group and a user applies to anyone
        matching EITHER -- a union, not an intersection."""
        group = self.env['res.groups'].create({'name': 'FOI Test Group'})
        in_group_user = new_test_user(self.env, login='foi_union_group_user', groups='base.group_user')
        in_group_user.groups_id = [(4, group.id)]
        named_user = new_test_user(self.env, login='foi_union_named_user', groups='base.group_user')
        outsider = new_test_user(self.env, login='foi_union_outsider', groups='base.group_user')

        policy = self.env['foi.restriction.policy'].create({
            'name': 'Union Test Policy',
            'group_ids': [(6, 0, group.ids)],
            'user_ids': [(6, 0, named_user.ids)],
        })
        applicable_for_group_member = policy.with_user(in_group_user)._filter_applicable_to_current_user()
        applicable_for_named_user = policy.with_user(named_user)._filter_applicable_to_current_user()
        applicable_for_outsider = policy.with_user(outsider)._filter_applicable_to_current_user()

        self.assertTrue(applicable_for_group_member)
        self.assertTrue(applicable_for_named_user)
        self.assertFalse(applicable_for_outsider)


@tagged('post_install', '-at_install')
class TestFoiRestrictionRuleCompiler(TransactionCase):
    """Tests for compiling fixed-area tabs and domain lines into ir.rule."""

    def setUp(self):
        super().setUp()
        self.compiler = self.env['foi.restriction.rule.compiler']
        self.group = self.env['res.groups'].create({'name': 'FOI Compiler Test Group'})
        self.test_user = new_test_user(self.env, login='foi_compiler_test_user', groups='base.group_user')
        self.test_user.groups_id = [(4, self.group.id)]

    def _managed_rules(self, model_name=None):
        domain = [('name', 'like', 'FOI Restriction: ')]
        if model_name:
            ir_model = self.env['ir.model']._get(model_name)
            domain.append(('model_id', '=', ir_model.id))
        return self.env['ir.rule'].search(domain)

    def test_empty_whitelist_area_creates_no_rule(self):
        self.env['foi.restriction.policy'].create({
            'name': 'Empty Journals Policy',
            'group_ids': [(6, 0, self.group.ids)],
        })
        self.compiler.recompile_all()
        self.assertFalse(self._managed_rules('account.journal'))

    def test_whitelist_area_allows_only_selected_journals(self):
        journal = self.env['account.journal'].search([], limit=1)
        self.assertTrue(journal, "Test requires at least one account.journal record to exist.")
        self.env['foi.restriction.policy'].create({
            'name': 'Restrict Journals',
            'group_ids': [(6, 0, self.group.ids)],
            'journal_ids': [(6, 0, journal.ids)],
        })
        self.compiler.recompile_all()
        visible = self.env['account.journal'].with_user(self.test_user).search([])
        self.assertEqual(set(visible.ids), set(journal.ids))

    def test_blacklist_menu_hides_selected(self):
        menu = self.env['ir.ui.menu'].search([], limit=1)
        self.assertTrue(menu, "Test requires at least one ir.ui.menu record to exist.")
        self.env['foi.restriction.policy'].create({
            'name': 'Hide One Menu',
            'group_ids': [(6, 0, self.group.ids)],
            'menu_ids': [(6, 0, menu.ids)],
        })
        self.compiler.recompile_all()
        visible = self.env['ir.ui.menu'].with_user(self.test_user).search([('id', '=', menu.id)])
        self.assertFalse(visible)

    def test_hierarchical_category_selection_includes_children(self):
        parent = self.env['product.category'].create({'name': 'FOI Parent Category'})
        child = self.env['product.category'].create({'name': 'FOI Child Category', 'parent_id': parent.id})
        unrelated = self.env['product.category'].create({'name': 'FOI Unrelated Category'})
        self.env['foi.restriction.policy'].create({
            'name': 'Restrict To Parent Category Tree',
            'group_ids': [(6, 0, self.group.ids)],
            'product_categ_ids': [(6, 0, parent.ids)],
        })
        self.compiler.recompile_all()
        visible = self.env['product.category'].with_user(self.test_user).search([
            ('id', 'in', [parent.id, child.id, unrelated.id]),
        ])
        self.assertIn(parent.id, visible.ids)
        self.assertIn(child.id, visible.ids, "Selecting a parent category must include its children.")
        self.assertNotIn(unrelated.id, visible.ids)

    def test_vendor_restriction_does_not_affect_plain_contacts(self):
        vendor = self.env['res.partner'].create({'name': 'FOI Vendor', 'supplier_rank': 1})
        other_vendor = self.env['res.partner'].create({'name': 'FOI Other Vendor', 'supplier_rank': 1})
        plain_contact = self.env['res.partner'].create({'name': 'FOI Plain Contact'})
        self.env['foi.restriction.policy'].create({
            'name': 'Restrict To One Vendor',
            'group_ids': [(6, 0, self.group.ids)],
            'vendor_ids': [(6, 0, vendor.ids)],
        })
        self.compiler.recompile_all()
        visible = self.env['res.partner'].with_user(self.test_user).search([
            ('id', 'in', [vendor.id, other_vendor.id, plain_contact.id]),
        ])
        self.assertIn(vendor.id, visible.ids)
        self.assertNotIn(other_vendor.id, visible.ids)
        self.assertIn(plain_contact.id, visible.ids, "A non-vendor contact must be unaffected by the Vendors tab.")

    def test_vendor_and_customer_tabs_combine_correctly_for_dual_role_partner(self):
        """A partner that is BOTH a vendor and a customer must satisfy both
        whitelists once each tab is non-empty, since the audience is the
        same for both policies and they get AND-merged onto the same model.
        """
        dual = self.env['res.partner'].create({
            'name': 'FOI Dual Role Partner', 'supplier_rank': 1, 'customer_rank': 1,
        })
        allowed_vendor_only = self.env['res.partner'].create({'name': 'FOI Vendor Only', 'supplier_rank': 1})
        self.env['foi.restriction.policy'].create({
            'name': 'Vendor And Customer Whitelist',
            'group_ids': [(6, 0, self.group.ids)],
            'vendor_ids': [(6, 0, (dual | allowed_vendor_only).ids)],
            'customer_ids': [(6, 0, dual.ids)],
        })
        self.compiler.recompile_all()
        visible = self.env['res.partner'].with_user(self.test_user).search([
            ('id', 'in', [dual.id, allowed_vendor_only.id]),
        ])
        self.assertIn(dual.id, visible.ids)
        self.assertIn(allowed_vendor_only.id, visible.ids)

    def test_domain_line_deny_mode_excludes_matching_records(self):
        partner_model = self.env['ir.model']._get('res.partner')
        matching = self.env['res.partner'].create({'name': 'FOI Domain Denied Partner'})
        other = self.env['res.partner'].create({'name': 'FOI Domain Allowed Partner'})
        policy = self.env['foi.restriction.policy'].create({
            'name': 'Deny Via Domain Line',
            'group_ids': [(6, 0, self.group.ids)],
        })
        self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': partner_model.id,
            'mode': 'deny',
            'domain': "[('id', '=', %d)]" % matching.id,
        })
        self.compiler.recompile_all()
        visible = self.env['res.partner'].with_user(self.test_user).search([
            ('id', 'in', [matching.id, other.id]),
        ])
        self.assertNotIn(matching.id, visible.ids)
        self.assertIn(other.id, visible.ids)

    def test_domain_line_allow_mode_restricts_to_matching_records(self):
        partner_model = self.env['ir.model']._get('res.partner')
        matching = self.env['res.partner'].create({'name': 'FOI Domain Allowed Partner'})
        other = self.env['res.partner'].create({'name': 'FOI Domain Excluded Partner'})
        policy = self.env['foi.restriction.policy'].create({
            'name': 'Allow Via Domain Line',
            'group_ids': [(6, 0, self.group.ids)],
        })
        self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': partner_model.id,
            'mode': 'allow',
            'domain': "[('id', '=', %d)]" % matching.id,
        })
        self.compiler.recompile_all()
        visible = self.env['res.partner'].with_user(self.test_user).search([
            ('id', 'in', [matching.id, other.id]),
        ])
        self.assertIn(matching.id, visible.ids)
        self.assertNotIn(other.id, visible.ids)

    def test_domain_line_with_uid_enforces_own_records_per_actual_user(self):
        """Regression test for the compile-time evaluation bug: a domain
        referencing `uid` (e.g. "only my own records") must be evaluated
        per ACTUAL end user at query time, not baked in as whichever admin
        happened to click Recompile.
        """
        partner_model = self.env['ir.model']._get('res.partner')
        user_a = new_test_user(self.env, login='foi_uid_user_a', groups='base.group_user')
        user_b = new_test_user(self.env, login='foi_uid_user_b', groups='base.group_user')
        user_a.groups_id = [(4, self.group.id)]
        user_b.groups_id = [(4, self.group.id)]
        owned_by_a = self.env['res.partner'].create({'name': 'FOI Owned By A', 'user_id': user_a.id})
        owned_by_b = self.env['res.partner'].create({'name': 'FOI Owned By B', 'user_id': user_b.id})

        policy = self.env['foi.restriction.policy'].create({
            'name': 'Own Records Only',
            'group_ids': [(6, 0, self.group.ids)],
        })
        self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': partner_model.id,
            'mode': 'allow',
            'domain': "[('user_id', '=', uid)]",
        })
        # Compile as the test's admin user -- if `uid` were evaluated
        # eagerly here, it would incorrectly bake in the admin's id.
        self.compiler.recompile_all()

        visible_to_a = self.env['res.partner'].with_user(user_a).search([
            ('id', 'in', [owned_by_a.id, owned_by_b.id]),
        ])
        visible_to_b = self.env['res.partner'].with_user(user_b).search([
            ('id', 'in', [owned_by_a.id, owned_by_b.id]),
        ])
        self.assertEqual(visible_to_a.ids, [owned_by_a.id], "User A must see only their own record.")
        self.assertEqual(visible_to_b.ids, [owned_by_b.id], "User B must see only their own record.")

    def test_area_tab_and_domain_line_merge_for_same_audience(self):
        """A whitelist area tab and a domain-line deny, for the SAME
        audience and model, must both apply (AND-merged) rather than one
        silently overriding the other.
        """
        partner_model = self.env['ir.model']._get('res.partner')
        allowed_by_tab = self.env['res.partner'].create({
            'name': 'FOI Allowed By Tab', 'customer_rank': 1,
        })
        denied_by_domain = self.env['res.partner'].create({
            'name': 'FOI Denied By Domain', 'customer_rank': 1,
        })
        policy = self.env['foi.restriction.policy'].create({
            'name': 'Combined Tab And Domain Policy',
            'group_ids': [(6, 0, self.group.ids)],
            'customer_ids': [(6, 0, (allowed_by_tab | denied_by_domain).ids)],
        })
        self.env['foi.restriction.policy.domain.line'].create({
            'policy_id': policy.id,
            'target_id': partner_model.id,
            'mode': 'deny',
            'domain': "[('id', '=', %d)]" % denied_by_domain.id,
        })
        self.compiler.recompile_all()
        self.assertEqual(
            len(self._managed_rules('res.partner')), 1,
            "Same-audience contributions on the same model must merge into a single ir.rule.",
        )
        visible = self.env['res.partner'].with_user(self.test_user).search([
            ('id', 'in', [allowed_by_tab.id, denied_by_domain.id]),
        ])
        self.assertIn(allowed_by_tab.id, visible.ids)
        self.assertNotIn(
            denied_by_domain.id, visible.ids,
            "The domain-line deny must still apply even though the tab whitelisted this record.",
        )

    def test_recompile_is_idempotent(self):
        journal = self.env['account.journal'].search([], limit=1)
        self.assertTrue(journal)
        self.env['foi.restriction.policy'].create({
            'name': 'Idempotency Test Policy',
            'group_ids': [(6, 0, self.group.ids)],
            'journal_ids': [(6, 0, journal.ids)],
        })
        self.compiler.recompile_all()
        self.compiler.recompile_all()
        self.assertEqual(len(self._managed_rules('account.journal')), 1)

    def test_inactive_policy_is_not_compiled(self):
        journal = self.env['account.journal'].search([], limit=1)
        self.assertTrue(journal)
        self.env['foi.restriction.policy'].create({
            'name': 'Inactive Policy',
            'active': False,
            'group_ids': [(6, 0, self.group.ids)],
            'journal_ids': [(6, 0, journal.ids)],
        })
        self.compiler.recompile_all()
        self.assertFalse(self._managed_rules('account.journal'))

    def test_user_only_policy_compiles_via_carrier_group(self):
        specific_user = new_test_user(self.env, login='foi_carrier_user', groups='base.group_user')
        journal = self.env['account.journal'].search([], limit=1)
        self.assertTrue(journal)
        policy = self.env['foi.restriction.policy'].create({
            'name': 'User Only Journal Restriction',
            'user_ids': [(6, 0, specific_user.ids)],
            'journal_ids': [(6, 0, journal.ids)],
        })
        self.compiler.recompile_all()
        policy.invalidate_recordset()
        self.assertTrue(policy.compiled_audience_group_id.exists())
        self.assertIn(specific_user, policy.compiled_audience_group_id.users)

    def test_menu_recompile_clears_cache_without_error(self):
        menu = self.env['ir.ui.menu'].search([], limit=1)
        self.assertTrue(menu)
        self.env['foi.restriction.policy'].create({
            'name': 'Menu Cache Test Policy',
            'group_ids': [(6, 0, self.group.ids)],
            'menu_ids': [(6, 0, menu.ids)],
        })
        # Should not raise.
        self.compiler.recompile_all()
        self.assertTrue(self._managed_rules('ir.ui.menu'))
