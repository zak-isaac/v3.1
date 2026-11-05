"""IND3 Next Gen signature, ported verbatim from the live V3 workshop app
(signatures.py) so the V3.1 passdown EML matches V3's real Outlook output.

Table-based, inline-style HTML for Outlook. Does not alter report sections.
The small Spark glyph is referenced by Content-ID; embed_spark_in_eml()
attaches ind3-walmart-spark.png as an inline MIME image with that CID.

Exact-name contact mapping only; never guess employee IDs or email
addresses for someone not in KNOWN.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

SIGNATURE_MARKER = "<!-- IND3-NEXT-GEN-SIGNATURE -->"
SPARK_CID = "ind3-walmart-spark"
FACILITY = "eCommerce Fulfillment Center # 7552/8972"
ADDRESS = "5259 W. 500 North McCordsville, Indiana, 46055"
SPARK_PNG = Path(__file__).resolve().parent / "web" / "ind3-walmart-spark.png"
# name -> (role, email, Walmart default/NT ID). The ID is what actually
# appears in the signature ("Full Name - defaultid"); role/email are kept
# for callers that still want them but are not rendered in this design.
KNOWN = {
    "isaac ssaku": ("Maintenance Technician", "isaac.ssaku@walmart.com", "ins0002"),
    "stanley augustin": ("Maintenance Technician", "stanley.augustin@walmart.com", "s0a00ns"),
    "alexander carr": ("Maintenance Technician", "Alexander.Carr@walmart.com", "acarr1"),
    "len ward": ("Maintenance Technician", "len.ward@walmart.com", "ldw006f"),
    "kevin smith": ("Maintenance Technician", "Kevin.Smith1@walmart.com", "k0s08q0"),
}


def get_signature_html(lead_name: str, *, spark_src: str = f"cid:{SPARK_CID}") -> str:
    """Return a standalone signature; spark_src may be a data URL for preview."""
    name = (lead_name or "").strip() or "IND3 Maintenance Team"
    _, _, known_id = KNOWN.get(name.casefold(), ("", "", ""))
    safe_name = html.escape(name)
    id_html = f" - {html.escape(known_id)}" if known_id else ""
    safe_src = html.escape(spark_src, quote=True)
    line_style = "font-family:Georgia,'Times New Roman',serif;font-weight:700;color:#4472c4;"

    return SIGNATURE_MARKER + f"""<table role="presentation" cellspacing="0" cellpadding="0" border="0"
      style="width:100%;max-width:560px;margin:20px 0 0;border-collapse:collapse;">
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 4px;">{safe_name}{id_html}</td></tr>
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 4px;">{html.escape(FACILITY)}</td></tr>
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 10px;">{html.escape(ADDRESS)}</td></tr>
      <tr><td style="padding:0 0 6px;">
        <span style="{line_style}font-style:italic;font-size:19px;line-height:24px;">Walmart</span>
        <img src="{safe_src}" width="16" height="16" alt="Spark"
         style="display:inline-block;width:16px;height:16px;border:0;vertical-align:middle;margin-left:4px;" />
      </td></tr>
      <tr><td style="font-family:Georgia,'Times New Roman',serif;font-weight:700;
        font-style:italic;font-size:19px;line-height:24px;">
        <span style="color:#c0491e;">Save money.</span>
        <span style="color:#8b6f1a;"> Live better</span>
      </td></tr>
    </table>"""


def append_signature(body_html: str, lead_name: str) -> str:
    """Append the signature once; do not modify the existing report body."""
    if SIGNATURE_MARKER in body_html:
        return body_html
    sig = get_signature_html(lead_name)
    return re.sub(r"</body\s*>", lambda m: sig + m.group(0), body_html,
                  count=1, flags=re.IGNORECASE) if re.search(
                      r"</body\s*>", body_html, re.IGNORECASE
                  ) else body_html + sig


def embed_spark_cid(html_part) -> None:
    """Attach the approved Spark PNG as an inline CID image on an EmailMessage
    HTML sub-part (e.g. the part returned by msg.get_payload()[-1] right
    after add_alternative), only if the body actually references the CID.
    """
    content = html_part.get_content()
    if f"cid:{SPARK_CID}" not in content or not SPARK_PNG.exists():
        return
    html_part.add_related(
        SPARK_PNG.read_bytes(), maintype="image", subtype="png",
        cid=f"<{SPARK_CID}>", disposition="inline", filename="ind3-walmart-spark.png",
    )
