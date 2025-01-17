# -*- coding: utf8 -*-
#
#    Copyright (C) 2015 NDP Systèmes (<http://www.ndp-systemes.fr>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from openerp import models, fields, api


class ManufacturingOrderPlanningImproved(models.Model):
    _inherit = 'mrp.production'

    date_planned = fields.Datetime(readonly=False,
                                   states={'done': [('readonly', True)], 'cancel': [('readonly', True)]})
    date_required = fields.Datetime(string="Date required",
                                    states={'done': [('readonly', True)], 'cancel': [('readonly', True)]})
    taken_into_account = fields.Boolean(string="Taken into account",
                                        help="True if the manufacturing order has been taken into account",
                                        states={'done': [('readonly', True)], 'cancel': [('readonly', True)]})
    procurement_id = fields.Many2one('procurement.order', string="Corresponding procurement order",
                                                                                    compute='_compute_procurement_id')

    @api.multi
    def _compute_procurement_id(self):
        for rec in self:
            list_procurements = self.env['procurement.order'].search([('production_id', '=', rec.id)])
            if list_procurements and len(list_procurements) == 1:
                rec.procurement_id = list_procurements[0]

    @api.model
    def _make_production_produce_line(self, production):
        """Overridden here so that the date of the produce stock move is the date of end of the production."""
        move_id = super(ManufacturingOrderPlanningImproved, self)._make_production_produce_line(production)
        date_move = fields.Datetime.to_string(production.location_dest_id.schedule_working_days(
            production.product_id.produce_delay + 1,
            fields.Datetime.from_string(production.date_planned)
        ))
        move = self.env['stock.move'].browse(move_id)
        move.write({'date_expected': date_move, 'date': date_move})
        return move_id

    @api.multi
    def write(self, vals):
        result = super(ManufacturingOrderPlanningImproved, self).write(vals)
        if vals.get('date_planned'):
            for rec in self:
                rec.move_created_ids.write({'date_expected': vals['date_planned']})
        return result

    @api.multi
    def action_reschedule(self):
        for rec in self:
            values = {}
            if rec.taken_into_account:
                values['date'] = min(rec.date_planned, rec.date_required)
            else:
                values['date'] = rec.date_required
            if self.env.context.get('reschedule_planned_date'):
                values['date_expected'] = rec.date_required
            rec.move_lines.write(values)


class ProcurementOrderPlanningImproved(models.Model):
    _inherit = 'procurement.order'

    @api.model
    def _prepare_mo_vals(self, procurement):
        result = super(ProcurementOrderPlanningImproved, self)._prepare_mo_vals(procurement)
        result['date_required'] = result.get('date_planned', False)
        return result

    @api.multi
    def action_reschedule(self):
        """Reschedules the moves associated to this procurement."""
        for proc in self:
            if proc.state not in ['done', 'cancel'] and proc.rule_id and proc.rule_id.action == 'manufacture':
                if proc.production_id:
                    prod_start_date = self.env['procurement.order']._get_date_planned(proc)
                    prod_end_date = proc.production_id.location_dest_id.schedule_working_days(
                        -proc.company_id.manufacturing_lead,
                        fields.Datetime.from_string(proc.date_planned)
                    )
                    # Updating the dates of the created moves of the corresponding manufacturing order
                    proc.production_id.move_created_ids.write({'date': prod_end_date})
                    # Updating the date_required of the corresponding manufacturing order
                    proc.production_id.date_required = prod_start_date
                    # Updating the date_planned of the corresponding manufacturing order
                    if self.env.context.get('reschedule_planned_date') and not proc.production_id.taken_into_account:
                        proc.production_id.date_planned = prod_start_date
        super(ProcurementOrderPlanningImproved, self).action_reschedule()
