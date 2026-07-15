# -*- coding: utf-8 -*-
import time
import datetime
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


class FoiRestrictionPolicyDomainLine(models.Model):
    """One arbitrary allow/deny domain rule against any model, attached to
    a policy's audience.

    This is the generic escape hatch: it's how the framework stays
    extensible to any model -- including ones not covered by the fixed
    business-area tabs -- without any registration step and without ever
    needing a core code change here. Pick the model directly.

    IMPORTANT: the domain string may reference dynamic, per-request
    variables the same way a native ir.rule does -- `uid`, `user`, `time`,
    `datetime`, `relativedelta`, `context_today`. These are NOT resolved
    when the policy is saved or compiled; they are only ever evaluated by
    Odoo's own security engine at actual query time, once compiled into a
    native ir.rule (see foi.restriction.rule.compiler, which is written to
    preserve these references verbatim rather than evaluating them eagerly
    at compile time -- evaluating `uid` while an admin is compiling would
    bake in the ADMIN's id, not the end user's, which would be a serious
    correctness bug for exactly the most common restriction pattern:
    "each user sees only their own records", e.g. [('user_id', '=', uid)].
    """

    _name = 'foi.restriction.policy.domain.line'
    _description = 'Restriction Policy Domain Rule'
    _rec_name = 'target_id'

    policy_id = fields.Many2one(
        'foi.restriction.policy', required=True, ondelete='cascade', index=True,
    )
    target_id = fields.Many2one(
        'ir.model', required=True, ondelete='cascade', string='Model',
        domain=[('transient', '=', False)],
        help="Which model this rule applies to. Any installed model can be "
             "picked directly -- no separate registration needed.",
    )
    model_name = fields.Char(related='target_id.model', store=True, readonly=True)
    mode = fields.Selection([
        ('allow', 'Allow only matching records'),
        ('deny', 'Deny matching records'),
    ], required=True, default='deny')
    domain = fields.Char(
        default='[]', required=True,
        help="Domain against the target model, e.g. \"[('user_id', '=', uid)]\" "
             "for 'only my own records'. Same syntax and dynamic variables "
             "(uid, user, time, datetime, relativedelta, context_today) as "
             "a native ir.rule domain.",
    )

    def _domain_eval_context(self):
        """Same shape of context a native ir.rule domain is evaluated with,
        used here only to validate SYNTAX at save time -- not to bake any
        of these values into the compiled rule (see class docstring).
        """
        return {
            'uid': self.env.uid,
            'user': self.env.user,
            'time': time,
            'datetime': datetime,
            'relativedelta': relativedelta,
            'context_today': fields.Date.context_today,
        }

    @api.constrains('domain')
    def _check_domain_syntax(self):
        for line in self:
            try:
                safe_eval(line.domain or '[]', line._domain_eval_context())
            except Exception as exc:
                raise ValidationError(_(
                    "Invalid domain on a rule for '%s': %s"
                ) % (line.target_id.model, exc))
