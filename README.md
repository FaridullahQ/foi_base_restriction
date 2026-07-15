# FOI Base Restriction

Audience-based visibility restrictions for Odoo 17 Community: pick who a policy applies to (Groups and/or Users), then configure what they can see across a set of fixed business-area tabs, plus one advanced tab for anything else.

## Requirements

- Odoo 17.0 (Community)
- Depends on: `base`, `mail`, `product`, `stock`, `account`

## Installation

1. Copy `foi_base_restriction` into your custom addons path.
2. Update the apps list, then install **FOI Base Restriction**.
3. Assign the **Restriction Manager** group (Settings → Users) to whoever will configure policies.

## How it works

One record = one audience. Go to **Restrictions → Policies → New**:

1. **Applies to Groups / Applies to Users** — who this policy affects. Leave both empty for everyone. The two are combined as a union (matches *either*), not an intersection.
2. Walk through the tabs to decide what that audience can see.
3. Click **Restrictions → Recompile Restriction Rules** to apply changes.

### Fixed tabs and their default semantics

| Tab | Semantic | Empty selection means |
|---|---|---|
| Menus | **Blacklist** — selected items are hidden | All menus visible |
| Products / Product Variants | **Whitelist** — only selected are allowed | All allowed |
| Product Categories | **Whitelist**, includes sub-categories automatically | All allowed |
| Vendors | **Whitelist**, only affects `res.partner` records with `supplier_rank > 0` | All vendors allowed |
| Customers | **Whitelist**, only affects `res.partner` records with `customer_rank > 0` | All customers allowed |
| Journals | **Whitelist** | All allowed |
| Warehouses | **Whitelist** | All allowed |
| Locations | **Whitelist**, includes sub-locations automatically | All allowed |

Vendors and Customers share the same underlying model (`res.partner`) but restrict independently — a plain contact that is neither is unaffected by either tab.

### Domain Rules (Advanced)

For anything the fixed tabs don't cover — other models, or conditions the whitelist/blacklist pattern can't express. Pick any installed model directly (no registration step), choose Allow or Deny, and write a domain using the same dynamic variables a native Odoo Record Rule supports:

```
uid, user, time, datetime, relativedelta, context_today()
```

Example — "each user sees only their own records":

```python
[('user_id', '=', uid)]
```

This is evaluated fresh per actual end user at query time, exactly like a native `ir.rule` — never resolved eagerly when the policy is saved or compiled.

## Precedence rules

- **Same audience, same model, multiple contributions** (from any combination of fixed tabs and Domain Rules) are merged with **AND** — the most restrictive contribution always wins. A `Deny` can never be silently overridden by an `Allow` for the same audience.
- **Different audiences** compile to separate native `ir.rule` records, which Odoo ORs together — matching ordinary Odoo behavior where an additional group membership can widen access.

## Architecture

- `foi.restriction.policy` — the audience + fixed-tab selections.
- `foi.restriction.policy.domain.line` — one advanced domain rule, attached to a policy.
- `foi.restriction.rule.compiler` — compiles both into native `ir.rule` records. Domain contributions are merged as unevaluated source text (via Python's `ast` module), never `eval`'d at compile time, so dynamic expressions like `uid` are preserved correctly for per-user evaluation later.
- A policy naming specific Users (not just Groups) gets a small, hidden, compiler-managed "carrier" `res.groups` record under the hood, since native `ir.rule` only understands groups. This is automatic — no manual group management needed.

All actual enforcement is delegated to Odoo's native `ir.rule` engine, so restrictions apply consistently everywhere Odoo's own security already applies: UI, search, RPC, reports, and import/export.

## Known limitations

- **Menus**: visibility is cached by Odoo for performance. Recompiling clears that cache server-side, but users already logged in may need to refresh or log back in to see menu changes.
- **This module does not intercept native UI buttons** (e.g. the standard list-view Export button) — it restricts *data visibility* via `ir.rule`. If a user has native export rights and can see a record, they can export it; hiding the record via a Vendors/Customers/etc. tab is what actually prevents that record from being exported, printed, or acted on at all.
- No migration path is provided between schema versions — this module has changed shape significantly during development. Treat major version bumps as requiring policies to be re-entered rather than upgraded in place.
- Domain Rules validation checks syntax at save time but cannot verify that a dynamic expression will behave as intended for every user — test with a real non-admin account before relying on a policy in production.

## Running tests

```bash
odoo-bin -d <your_database> -i foi_base_restriction --test-enable --stop-after-init
```

Test coverage lives in `tests/test_foi_restriction.py`: policy/domain-line validation, fixed-tab compilation (whitelist/blacklist, hierarchical expansion, vendor/customer isolation), domain-line allow/deny including dynamic `uid` expressions, same-audience precedence merging, and carrier-group handling for user-only audiences.

## License

LGPL-3
