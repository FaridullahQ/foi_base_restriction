# -*- coding: utf-8 -*-
{
    'name': 'FOI Base Restriction',
    'version': '17.0.2.0.0',
    'category': 'Tools',
    'summary': 'Audience-based visibility restrictions: menus, products, partners, journals, warehouses',
    'description': """
Restriction Policies
=====================

One record per audience (Groups and/or Users). Configure what that audience
can see across fixed tabs:

* Menus                        -- selected items are HIDDEN, empty = all visible
* Products / Product Variants  -- selected are the ONLY ones allowed, empty = all allowed
* Product Categories           -- same, and selecting a category includes its subtree
* Vendors / Customers          -- same, restricted to res.partner records of that kind only
* Journals / Warehouses / Locations -- same

Plus one advanced tab, Domain Rules, for anything else: pick any model
directly and write an arbitrary allow/deny domain -- including dynamic,
per-user expressions like [('user_id', '=', uid)] for "only my own records",
exactly like a native Record Rule.

Everything is compiled into native ir.rule records (Restrictions > Recompile
Restriction Rules), so restrictions apply consistently everywhere Odoo's own
security engine already applies: the UI, search, RPC, reports, and
import/export.
    """,
    'author': 'FOI',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'product', 'stock', 'account'],
    'data': [
        'security/foi_restriction_security.xml',
        'security/ir.model.access.csv',
        'views/foi_restriction_policy_views.xml',
        'views/foi_restriction_menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
