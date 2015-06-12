# -*- coding: utf8 -*-
#
# Copyright (C) 2014 NDP Systèmes (<http://www.ndp-systemes.fr>).
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

from datetime import datetime
from openerp.osv import osv

from openerp.tools import DEFAULT_SERVER_DATE_FORMAT
from openerp import fields, models, api, _

class purchase_order_jit(models.Model):
    _inherit = 'purchase.order'

    order_line = fields.One2many(states={'done':[('readonly',True)]})

    @api.one
    def _create_stock_moves_improved(self, order, order_lines, group_id=False, picking_id=False):
        todo_moves = []
        if not group_id:
            group_id = self.env['procurement.group'].create({'name': order.name, 'partner_id': order.partner_id.id})

        for order_line in order_lines:
            if not order_line.product_id:
                continue

            if order_line.product_id.type in ('product', 'consu'):
                for vals in self._prepare_order_line_move(order, order_line, picking_id.id, group_id.id):
                    move = self.env['stock.move'].create(vals)
                    todo_moves.append(move)
        for move in todo_moves:
            move.action_confirm()
            move.force_assign()

class purchase_order_line_jit(models.Model):
    _inherit = 'purchase.order.line'

    line_no = fields.Char("Line no.")
    ack_ref = fields.Char("Acknowledge Reference", help="Reference of the supplier's last reply to confirm the delivery"
                                                        " at the planned date")
    date_ack = fields.Date("Last Acknowledge Date",
                           helps="Last date at which the supplier confirmed the delivery at the planned date.")
    opmsg_type = fields.Selection([('no_msg',"Ok"), ('late',"LATE"), ('early',"EARLY")], compute="_compute_opmsg",
                                  string="Message Type")
    opmsg_delay = fields.Integer("Message Delay", compute="_compute_opmsg")
    opmsg_reduce_qty = fields.Float("New target quantity after procurement cancellation", readonly=True, default=False)
    opmsg_text = fields.Char("Operational message", compute="_compute_opmsg_text",
                         help="This field holds the operational messages generated by the system to the operator")
    remaining_qty = fields.Float("Remaining quantity", help="Quantity not yet delivered by the supplier", compute="_get_remaining_qty", store=False)
    to_delete = fields.Boolean('True if all the procurements of the purchase order line are canceled')
    father_line_id = fields.Many2one('purchase.order.line', string="Very first line splited")
    children_number = fields.Integer(string="Number of children", default=0)
    po_date = fields.Date("Requested date", help="Order date of the corresponding purchase order",
                          default=fields.Date.context_today, states={'sent':[('readonly',True)],
                                                                  'bid':[('readonly',True)],
                                                                  'confirmed':[('readonly',True)],
                                                                  'approved':[('readonly',True)],
                                                                  'except_picking':[('readonly',True)],
                                                                  'except_invoice':[('readonly',True)],
                                                                  'done':[('readonly',True)],
                                                                  'cancel':[('readonly',True)],
                                                                  })

    @api.depends('date_planned','date_required')
    def _compute_opmsg(self):
        for rec in self:
            if rec.date_planned and rec.date_required:
                date_planned = datetime.strptime(rec.date_planned, DEFAULT_SERVER_DATE_FORMAT)
                date_required = datetime.strptime(rec.date_required, DEFAULT_SERVER_DATE_FORMAT)
                min_late_days = int(self.env['ir.config_parameter'].get_param(
                                                                  "purchase_procurement_just_in_time.opmsg_min_late_delay"))
                min_early_days = int(self.env['ir.config_parameter'].get_param(
                                                                 "purchase_procurement_just_in_time.opmsg_min_early_delay"))
                if date_planned >= date_required:
                    delta = date_planned - date_required
                    if delta.days >= min_late_days:
                        rec.opmsg_type = 'late'
                        rec.opmsg_delay = delta.days
                else:
                    delta = date_required - date_planned
                    if delta.days >= min_early_days:
                        rec.opmsg_type = 'early'
                        rec.opmsg_delay = delta.days

    @api.depends('opmsg_type','opmsg_delay','opmsg_reduce_qty', 'product_qty', 'to_delete', 'state')
    def _compute_opmsg_text(self):
        for rec in self:
            msg = ""
            if rec.to_delete and rec.product_qty != 0:
                msg += _("REDUCE QTY to %.1f %s ") % (0.0, rec.product_uom.name)
            if not rec.to_delete and rec.opmsg_reduce_qty and rec.opmsg_reduce_qty < rec.product_qty:
                msg += _("REDUCE QTY to %.1f %s ") % (rec.opmsg_reduce_qty, rec.product_uom.name)
            if rec.opmsg_type == 'early':
                msg += _("EARLY by %i day(s)") % rec.opmsg_delay
            elif rec.opmsg_type == 'late':
                msg += _("LATE by %i day(s)") % rec.opmsg_delay
            rec.opmsg_text = msg

    @api.multi
    def open_form_purchase_order_line(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order.line',
            'name': _("Purchase Order Line: %s") % self.line_no,
            'views': [(False, "form")],
            'res_id': self.id,
            'context': {}
        }

    @api.depends('move_ids.product_qty')
    def _get_remaining_qty(self):
        for rec in self:
            delivered_qty = sum([move.product_uom_qty for move in rec.move_ids if move.state == 'done'])
            rec.remaining_qty = rec.product_qty - delivered_qty

    @api.model
    def create(self, vals):
        maximum = 0
        if not vals.get('line_no', False):
            list_line_no = []
            list=[]
            order = self.env['purchase.order'].browse(vals['order_id'])
            for line in order.order_line:
                list += [line.line_no]
            for item in [l.line_no for l in order.order_line]:
                try:
                    list_line_no.append(int(item))
                except ValueError:
                    pass
            theo_value = 10*(1 + len(self.env['purchase.order'].browse(vals['order_id']).order_line))
            if list_line_no != []:
                maximum = max(list_line_no)
            if maximum >= theo_value or theo_value in list_line_no:
                theo_value = maximum + 10
            vals['line_no'] = str(theo_value)
        result = super(purchase_order_line_jit, self).create(vals)
        if result.order_id.state not in ['draft', 'done', 'cancel']:
            list_lines = [x for x in result.order_id.order_line if x != result]
            if list_lines != []:
                result.order_id.set_order_line_status(list_lines[0].state)
                group = False
                for line in list_lines:
                    for move in line.move_ids:
                        if move.state not in ['done', 'cancel']:
                            group = move.group_id
                            picking = move.picking_id
                            break
            result.order_id.set_order_line_status('confirmed')
            if result.product_qty != 0 and not result.move_ids:
                result.order_id._create_stock_moves_improved(result.order_id, result, group_id=group, picking_id=picking)
        return result

    @api.one
    def update_moves(self, vals):
        global_qty_ordered = sum(x.product_uom_qty for x in self.move_ids if x.state != 'cancel')
        if self.state == 'confirmed':
            if vals['product_qty'] == 0:
                for move in self.move_ids:
                    move.with_context({'cancel_procurement': True}).action_cancel()
            if vals['product_qty'] > 0:
                if vals.get('product_qty') > global_qty_ordered:
                    diff = vals.get('product_qty') - global_qty_ordered
                    move_without_proc_id = [x for x in self.move_ids if not x.procurement_id]
                    move_with_proc_id = [x for x in self.move_ids if x.procurement_id]
                    if len(move_without_proc_id + move_with_proc_id) != 0:
                        if len(move_without_proc_id) == 0:
                            new_move = move_with_proc_id[0].copy({'product_uom_qty': diff})
                            new_move.procurement_id = False
                            self.move_ids = self.move_ids + new_move
                            new_move.action_confirm()
                            new_move.action_assign()
                        else:
                            move = move_without_proc_id[0]
                            move.product_uom_qty = move.product_uom_qty + diff
                    else:
                        self.order_id._create_stock_moves(self.order_id, self)
                if vals.get('product_qty') < global_qty_ordered:
                    moves_without_proc_id = self.move_ids.filtered(lambda m: m.state != 'done' and not m.procurement_id).sorted(key=lambda m: m.product_qty, reverse=True)
                    moves_with_proc_id = self.move_ids.filtered(lambda m: m.state != 'done' and m.procurement_id).sorted(key=lambda m: m.product_qty, reverse=True)
                    if len(moves_without_proc_id + moves_with_proc_id) != 0:
                        _sum = sum([x.product_uom_qty for x in self.move_ids if x.state != 'cancel'])
                        while _sum > vals.get('product_qty'):
                            if len(moves_without_proc_id) > 0:
                                if moves_without_proc_id[0].product_uom_qty > _sum - vals.get('product_qty'):
                                    moves_without_proc_id[0].product_uom_qty = moves_without_proc_id[0].product_uom_qty - _sum + vals.get('product_qty')
                                    _sum -= _sum - vals.get('product_qty')
                                else:
                                    moves_without_proc_id[0].with_context({'cancel_procurement': True}).action_cancel()
                                    _sum -= moves_without_proc_id[0].product_uom_qty
                                    moves_without_proc_id -= moves_without_proc_id[0]
                            else:
                                if moves_with_proc_id[0].product_uom_qty > _sum - vals.get('product_qty'):
                                    moves_with_proc_id[0].product_uom_qty = moves_with_proc_id[0].product_uom_qty - _sum + vals.get('product_qty')
                                    _sum -= _sum - vals.get('product_qty')
                                else:
                                    _sum -= moves_with_proc_id[0].product_uom_qty
                                    moves_with_proc_id[0].product_uom_qty = 0.0
                                    moves_with_proc_id -= moves_with_proc_id[0]
                        if _sum < vals.get('product_qty'):
                            diff = vals.get('product_qty') - _sum
                            if len(moves_without_proc_id) == 0:
                                new_move = moves_with_proc_id[0].copy({'product_uom_qty': diff})
                                new_move.procurement_id = False
                                self.move_ids = self.move_ids + new_move
                                new_move.action_confirm()
                                new_move.action_assign()
                            else:
                                move = moves_without_proc_id[0]
                                move.product_uom_qty = move.product_uom_qty + diff
            move_to_remove = [x for x in self.move_ids if x.state == 'cancel']
            if len(move_to_remove) > 0:
                for move in move_to_remove:
                    self.move_ids = self.move_ids - move
            order = self.order_id
            if len(self.move_ids) == 0:
                self.action_cancel()
            if len(order.order_line) == 0:
                order.action_cancel()

    @api.multi
    def write(self, vals):
        result = super(purchase_order_line_jit, self).write(vals)
        if len(vals) == 1 and 'product_qty' in vals:
            if vals['product_qty'] < sum([x.product_uom_qty for x in self.move_ids if x.state == 'done']):
                raise osv.except_osv(_('Error!'),_("Impossible to cancel moves at state done."))
            for object in self:
                object.update_moves(vals)
        return result

class procurement_order_purchase_jit(models.Model):
    _inherit = 'procurement.order'

    @api.model
    def propagate_cancel(self, procurement):
        state = False
        if procurement.rule_id.action == 'buy' and procurement.purchase_line_id:
            state = procurement.purchase_line_id.state
            if procurement.purchase_line_id.state not in ['draft', 'cancel']:
                qty = procurement.purchase_line_id.product_qty
                procurement.purchase_line_id.state = 'draft'
        result = super(procurement_order_purchase_jit, self).propagate_cancel(procurement)
        if state:
            procurement.purchase_line_id.state = state
        if procurement.rule_id.action == 'buy' and procurement.purchase_line_id and procurement.purchase_line_id.state not in ['draft', 'cancel']:
            procurement.purchase_line_id.product_qty = qty
            total_need = sum([x.product_qty for x in procurement.purchase_line_id.procurement_ids if x.state != 'cancel' and x != procurement])
            if total_need != 0:
                list_supplierinfo_ids = self.env['product.supplierinfo'].search([('name', '=', procurement.purchase_line_id.order_id.partner_id.id),
                                                    ('product_tmpl_id', '=', procurement.product_id.product_tmpl_id.id)])
                supplierinfo = list_supplierinfo_ids[0]
                min_qty, packaging_qty = supplierinfo.min_qty, supplierinfo.packaging_qty
                total_need = max(total_need, min_qty)
                if total_need % packaging_qty != 0:
                    total_need = (total_need//packaging_qty + 1) * packaging_qty
            procurement.purchase_line_id.opmsg_reduce_qty = total_need
            if total_need == 0:
                procurement.purchase_line_id.to_delete = True
        return result

    @api.one
    def cancel(self):
        qty = False
        if self.purchase_line_id and self.purchase_line_id.order_id.state != 'draft':
            qty = self.purchase_line_id.product_qty
        result = super(procurement_order_purchase_jit, self).cancel()
        if self.purchase_line_id and self.purchase_line_id.order_id.state == 'draft':
            # in this case, purchase order is not yet confirmed
            line = self.purchase_line_id
            order = line.order_id
            global_qty = 0
            for order_line in order.order_line:
                for proc in order_line.procurement_ids:
                    if proc.product_id == line.product_id and proc != self and proc.state != 'cancel':
                        global_qty += proc.product_qty
            if line.state == 'draft' and global_qty == 0:
                line.unlink()
            if not order.order_line:
                order.unlink()
        if qty:
            self.purchase_line_id.product_qty = qty
        return result

class split_line(models.TransientModel):
    _name = 'split.line'
    _description = "Split Line"

    def get_pol(self):
        return self.env['purchase.order.line'].browse(self.env.context.get('active_id'))

    line_id = fields.Many2one('purchase.order.line', string="Direct parent line in purchase order", default=get_pol, required=True, readonly=True)
    qty = fields.Float(string="New quantity of direct parent line")

    @api.multi
    def do_split(self):
        if len(self) > 1:
            raise osv.except_osv(_('Error!'),_("Please split lines one by one"))
        else:
            if self.qty <= 0:
                raise osv.except_osv(_('Error!'),_("Impossible to split a negative or null quantity"))
            if self.line_id.state not in ['draft', 'confirmed']:
                raise osv.except_osv(_('Error!'),_("Impossible to split line which is not in state draft or confirmed"))
            if self.line_id.state == 'draft':
                if self.qty >= self.line_id.product_qty:
                    raise osv.except_osv(_('Error!'),_("Please choose a lower quantity to split"))
                diff = self.line_id.product_qty - self.qty
                self.line_id.product_qty = self.qty
                new_line = self.line_id.copy({'product_qty': diff})
                if not self.line_id.father_line_id:
                    new_line.father_line_id = self.line_id
                new_line.father_line_id.children_number = new_line.father_line_id.children_number + 1
                new_line.line_no = new_line.father_line_id.line_no + ' - ' + str(new_line.father_line_id.children_number)
            if self.line_id.state == 'confirmed':
                list_moves = self.line_id.move_ids.filtered(lambda m: m.state not in ['draft', 'done', 'cancel']).sorted(key=lambda m: m.product_qty)
                _sum = sum(x.product_uom_qty for x in self.line_id.move_ids if x.state == 'done')
                if self.qty < _sum:
                    raise osv.except_osv(_('Error!'),_("Impossible to split a move in state done"))
                if self.qty >= sum([m.product_uom_qty for m in self.line_id.move_ids if m.state != 'cancel']):
                    raise osv.except_osv(_('Error!'),_("Please choose a lower quantity to split"))
                moves_to_keep = []
                move_to_split = False
                if _sum != self.qty:
                    for move in list_moves:
                        _sum += move.product_uom_qty
                        if _sum > self.qty:
                            move_to_split = move
                            break
                        if _sum == self.qty:
                            moves_to_keep += [move]
                            break
                        else:
                            moves_to_keep += [move]
                new_pol = self.line_id.copy({'product_qty': 0, 'move_ids': False})
                if not self.line_id.father_line_id:
                    new_pol.father_line_id = self.line_id
                new_pol.father_line_id.children_number = new_pol.father_line_id.children_number + 1
                new_pol.line_no = new_pol.father_line_id.line_no + ' - ' + str(new_pol.father_line_id.children_number)
                for move in self.line_id.move_ids:
                    if move not in moves_to_keep and move != move_to_split and move.state not in ['done', 'cancel']:
                        move.purchase_line_id = new_pol
                if move_to_split:
                    self.env['stock.move'].split(move_to_split, self.qty - sum([m.product_uom_qty for m in moves_to_keep]))
                    move_to_split.purchase_line_id = new_pol
                new_pol.product_qty = sum(x.product_uom_qty for x in new_pol.move_ids)
                self.line_id.product_qty = sum([x.product_uom_qty for x in self.line_id.move_ids])
                for move in self.line_id.move_ids:
                    if move.purchase_line_id and move.state != 'assigned':
                        move.action_assign()
                self.line_id.order_id.set_order_line_status(self.line_id.state)