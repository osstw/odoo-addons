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

from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from openerp import fields, models, api


class res_partner_with_calendar(models.Model):
    _inherit = "res.partner"

    resource_id = fields.Many2one("resource.resource", "Supplier resource",
                                  help="The supplier resource is used to define the working days of the supplier when "
                                       "calculating lead times. If undefined here the system will consider working "
                                       "days of the supplier being Monday to Friday.")

    @api.multi
    def schedule_working_days(self, nb_days, day_date):
        """Returns the date that is nb_days working days after day_date in the context of the current supplier.

        :param nb_days: int: The number of working days to add to day_date. If nb_days is negative, counting is done
                             backwards.
        :param day_date: datetime: The starting date for the scheduling calculation.
        :return: The scheduled date nb_days after (or before) day_date.
        :rtype : datetime
        """
        self.ensure_one()
        assert isinstance(day_date, datetime)
        if nb_days == 0:
            return day_date

        calendar = False
        resource = self.resource_id
        if resource:
            calendar = resource.calendar_id
        if not calendar:
            calendar = self.env.ref("stock_working_days.default_calendar")

        newdate = calendar.schedule_days_get_date(nb_days, day_date=day_date,
                                                  resource_id=resource and resource.id or False,
                                                  compute_leaves=True)
        if isinstance(newdate, (list, tuple)):
            newdate = newdate[0]
        return newdate


class purchase_order_line_working_days(models.Model):
    _inherit = 'purchase.order.line'

    @api.model
    def _get_date_planned(self, supplier_info, date_order_str):
        """Return the datetime value to use as Schedule Date (``date_planned``) for
           PO Lines that correspond to the given product.supplierinfo,
           when ordered at `date_order_str`.
           Overridden here to calculate with working days.

           :param browse_record | False supplier_info: product.supplierinfo, used to
               determine delivery delay (if False, default delay = 0)
           :param str date_order_str: date of order field, as a string in
               DEFAULT_SERVER_DATETIME_FORMAT
           :rtype: datetime
           :return: desired Schedule Date for the PO line
        """
        order_date = datetime.strptime(date_order_str, DEFAULT_SERVER_DATETIME_FORMAT)
        # We add one day to supplier dalay because day scheduling counts the first day
        if supplier_info:
            return supplier_info.name.schedule_working_days(int(supplier_info.delay) + 1, order_date)
        else:
            return order_date


class purchase_working_days(models.Model):
    _inherit = "procurement.order"

    @api.model
    def _get_purchase_schedule_date(self, procurement, company):
        """Return the datetime value to use as Schedule Date (``date_planned``) for the
           Purchase Order Lines created to satisfy the given procurement.
           Overriden here to calculate dates taking into account the applicable working days calendar of our warehouse
           or our company.

           :param browse_record procurement: the procurement for which a PO will be created.
           :param browse_report company: the company to which the new PO will belong to.
           :rtype: datetime
           :return: the desired Schedule Date for the PO lines
        """
        proc_date = datetime.strptime(procurement.date_planned, DEFAULT_SERVER_DATETIME_FORMAT)
        location = procurement.location_id or procurement.warehouse_id.view_location_id
        schedule_date = location.schedule_working_days(-company.po_lead, proc_date)
        return schedule_date

    @api.model
    def _get_purchase_order_date(self, procurement, company, schedule_date):
        """Return the datetime value to use as Order Date (``date_order``) for the
           Purchase Order created to satisfy the given procurement.
           Overriden here to calculate dates taking into account the applicable working days calendar.

           :param browse_record procurement: the procurement for which a PO will be created.
           :param browse_report company: the company to which the new PO will belong to.
           :param datetime schedule_date: desired Scheduled Date for the Purchase Order lines.
           :rtype: datetime
           :return: the desired Order Date for the PO
        """
        seller_delay = int(procurement.product_id.seller_delay)
        partner = procurement.product_id.seller_id
        order_date = partner.schedule_working_days(-seller_delay, schedule_date)
        return order_date


