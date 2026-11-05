"""Canonical, immutable backend-authored V3.1 shift report and EML.

Never trust browser-generated report text as the archived source of truth.
Preview, saved download and Shift History share this serialized snapshot.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib

import v31_reporting
import v31_facility
import v31_pm_needing_completion as pm_needs
import v31_signature

TZ=ZoneInfo("America/Indiana/Indianapolis")

def build(conn,shift_id,shift_code,shift_day,private,lead_name=''):
    html=v31_reporting.build_passdown_html(conn,shift_id,shift_code,lead_name)
    settings=v31_facility.public_fields(v31_facility.load(private))
    dc=settings.get('distribution_center') or 'IND3'
    subject=f'{dc} - {shift_code} Shift Passdown - {shift_day}'
    msg=EmailMessage(policy=SMTP)
    # X-Unsent:1 matches the live V3 workshop app exactly -- it is what makes
    # desktop Outlook open the .eml directly in an editable compose window
    # instead of as a read-only received message.
    msg['X-Unsent']='1'
    msg['Subject']=subject
    # The unsent Outlook draft is addressed to the recipients saved under
    # Administration > Facility & Email Settings. Outlook supplies the From
    # account on send; V3.1 never sends mail automatically.
    recipients=v31_facility.parse_recipients(settings.get('recipients') or '')
    if recipients:
        msg['To']=', '.join(recipients)
    msg.set_content(f'{dc} {shift_code} End of Shift Passdown. Open the HTML part of this message.')
    msg.add_alternative(html,subtype='html',cte='8bit')
    v31_signature.embed_spark_cid(msg.get_payload()[-1])
    attached=pm_needs.get(conn,shift_id,include_data=True)
    if attached['present']:
        msg.add_attachment(attached['file_data'],maintype='application',
                           subtype='octet-stream',filename=attached['filename'])
    eml=msg.as_bytes()
    return {'html':html,'eml':eml,'sha256':hashlib.sha256(eml).hexdigest(),
            'filename':f'IND3_{shift_code}_{shift_day}_Shift_Passdown.eml',
            'created_at':datetime.now(TZ).isoformat(),
            'subject':subject,
            'attachments':[attached['filename']] if attached['present'] else []}
