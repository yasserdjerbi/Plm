##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

"""
Created on Apr 15, 2016

@author: Daniel Smerghetto
"""

from io import BytesIO
import base64
import os
import logging
from datetime import datetime
import time

from operator import itemgetter
from odoo import _
import odoo
from odoo import api
from odoo import models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT

from odoo.addons.plm.report.book_collector import BookCollector
from odoo.addons.plm.report.book_collector import getBottomMessage


def is_pdf(file_name):
    if os.path.splitext(file_name)[1].lower() == '.pdf':
        return True
    return False


def get_document_stream(doc_repository, obj_doc):
    """
        Gets the stream of a file
    """
    content = False
    try:
        if (not obj_doc.store_fname) and (obj_doc.db_datas):
            content = base64.b64decode(obj_doc.db_datas)
        else:
            with open(os.path.join(doc_repository, obj_doc.store_fname), 'rb') as f:
                content = f.read()
    except Exception as ex:
        logging.error("getFileStream : Exception ({0})reading  stream on file : {1}.".format(ex, obj_doc.datas_fname))
    return content


def _translate(value):
    return _(value)


def bom_sort(my_object):
    bom_object = []
    res = {}
    index = 0
    for l in my_object:
        res[str(index)] = l.itemnum
        index += 1
    items = list(res.items())
    items.sort(key=itemgetter(1))
    for res in items:
        bom_object.append(my_object[int(res[0])])
    return bom_object


def get_parent(my_object):
    return [my_object.product_tmpl_id.name,
            '',
            _(my_object.product_tmpl_id.name) or _(my_object.product_tmpl_id.default_code),
            my_object.product_tmpl_id.engineering_revision,
            _(my_object.product_tmpl_id.name),
            '',
            '',
            my_object.product_qty,
            '',
            my_object.weight_net,
            ]


class ReportSparePartsHeader(models.AbstractModel):
    _name = 'report.plm_spare.bom_spare_header'

    def get_document_brws(self, obj_product):
        oldest_obj = None
        oldest_dt = None
        if obj_product:
            for linkedBrwsDoc in obj_product.linkeddocuments:
                create_date_str = linkedBrwsDoc.create_date
                create_date = datetime.strptime(create_date_str, DEFAULT_SERVER_DATETIME_FORMAT)
                if oldest_dt is None or create_date < oldest_dt:
                    oldest_dt = create_date
                    oldest_obj = linkedBrwsDoc
        return oldest_obj

    def get_report_values(self, doc_ids, data={}):
        products = self.env['product.product'].browse(doc_ids)
        return {'docs': products,
                'time': time,
                'get_document_brws': self.get_document_brws}


class ReportSpareDocumentOne(models.AbstractModel):
    _name = 'report.plm_spare.pdf_one'
    """
    Calculates the bom structure spare parts manual
    """

    @api.model
    def create_spare_pdf(self, components):
        recursion = True
        if ReportSpareDocumentOne._name == 'report.plm_spare.pdf_one':
            recursion = False
        self.processed_objs = []

        component_type = self.env['product.product']
        bom_type = self.env['mrp.bom']
        user = self.env['res.users'].browse(self.env.uid)
        msg = getBottomMessage(user, self.env.context)
        main_book_collector = BookCollector(customTest=(True, msg))
        for component in components:
            self.processed_objs = []
            buf = self.get_first_page([component.id])
            main_book_collector.addPage((buf, ''))
            self.get_spare_parts_pdf_file(component, main_book_collector, component_type, bom_type, recursion)
        if main_book_collector is not None:
            pdf_string = BytesIO()
            main_book_collector.collector.write(pdf_string)
            out = pdf_string.getvalue()
            pdf_string.close()
            byte_string = b"data:application/pdf;base64," + base64.b64encode(out)
            return byte_string.decode('UTF-8')
        logging.warning('Unable to create PDF')
        return False, ''

    def get_spare_parts_pdf_file(self, product, output, component_template, bom_template, recursion):
        packed_objs = []
        packed_ids = []
        if product in self.processed_objs:
            return
        prod_tmpl_brws = bom_template.search([('product_id', '=', product.id), ('type', '=', 'spbom')])
        if len(prod_tmpl_brws) < 1:
            prod_tmpl_brws = bom_template.search([('product_tmpl_id', '=', product.product_tmpl_id.id), ('type', '=', 'spbom')])
        if len(prod_tmpl_brws) > 0:
            if prod_tmpl_brws:
                self.processed_objs.append(product)
                for bom_line in prod_tmpl_brws.bom_line_ids:
                    packed_objs.append(bom_line.product_id)
                    packed_ids.append(bom_line.id)
                if len(packed_ids) > 0:
                    for page_stream in self.get_pdf_component_layout(product):
                        try:
                            output.addPage((page_stream, ''))
                        except Exception as ex:
                            logging.error(ex)
                            raise ex
                    pdf = self.env.ref('plm.report_plm_bom_structure_one').sudo().render_qweb_pdf(prod_tmpl_brws.ids)[0]
                    page_stream = BytesIO()
                    page_stream.write(pdf)
                    output.addPage((page_stream, ''))
                    if recursion:
                        for packed_obj in packed_objs:
                            if packed_obj not in self.processed_objs:
                                self.get_spare_parts_pdf_file(packed_obj, output, component_template, bom_template, recursion)

    def get_pdf_component_layout(self, component):
        ret = []
        doc_repository = self.env['plm.document']._get_filestore()
        for document in component.linkeddocuments:
            if document.used_for_spare and (document.type == 'binary'):
                if document.printout and str(document.printout) != 'None':
                    ret.append(BytesIO(base64.b64decode(document.printout)))
                elif is_pdf(document.datas_fname):
                    value = get_document_stream(doc_repository, document)
                    if value:
                        ret.append(BytesIO(value))
        return ret

    def get_first_page(self, ids):
        str_buffer = BytesIO()
        pdf = self.env.ref('plm_spare.report_product_product_spare_header').sudo().render_qweb_pdf(ids)[0]
        str_buffer.write(pdf)
        return str_buffer

    @api.model
    def get_report_values(self, doc_ids, data={}):
        documents = self.env['product.product'].browse(doc_ids)
        return {'docs': documents,
                'get_content': self.create_spare_pdf}


class ReportSpareDocumentAll(ReportSpareDocumentOne):
    _name = 'report.plm_spare.pdf_all'
