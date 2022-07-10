# Copyright 2022 Graeme Gellatly
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models


class ContractAbstractContractLine(models.Model):

    _inherit = "contract.abstract.contract.line"
