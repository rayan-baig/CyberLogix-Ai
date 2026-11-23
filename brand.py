"""The product's name, the company's name, their addresses: one place.

Every page, email, text message, call script, agreement, sales sheet and
Pilot answer takes the names from here, so renaming the product or the
company is a change to the defaults below (or to the settings that
override them), not a hunt through the code. tests/test_brand.py fails if
a page, a script or a Python string spells one of the names anywhere else.

Each value may be set without a code change:

* CYBERLOGIX_APP_NAME        the product's full name ("Kryozyn")
* CYBERLOGIX_APP_SHORT_NAME  what a sentence calls it ("Kryozyn")
* CYBERLOGIX_COMPANY_NAME    the company behind it ("MTRX Horizyn")
* CYBERLOGIX_COMPANY_DOMAIN  the company's domain ("mtrxhorizyn.com")
* CYBERLOGIX_APP_HOST        the product's own address, a subdomain of the
                             company's ("kryozyn.mtrxhorizyn.com")
* CYBERLOGIX_CHECKLIST_APP   the company's food-safety checklist app, which
                             the Kitchen Safety Bundle fills in ("Aegizyn")
* CYBERLOGIX_SUPPORT_EMAIL   where customers write for help (live:
                             help@kryozyn.mtrxhorizyn.com; unset, the mail
                             reply-to address, see helpdesk.py)

Every setting also works as KRYOZYN_ plus the same rest (formerly.py), and
the KRYOZYN_ one wins when both are set.

The product was called CyberLogix AI until 3.34.0, at
cyberlogix.mtrxhorizyn.com; formerly.py keeps what was set up under that
name working. The company's new name is still being chosen: when it is,
COMPANY_NAME and COMPANY_DOMAIN below are the whole change.

The setting names themselves (CYBERLOGIX_*), the browser's storage keys and
the database file are identifiers, not names a person reads, and stay as
they are through a rename so nothing that is set up breaks. The API's
headers moved to X-Kryozyn-*, and the old X-CyberLogix-* names still work
(formerly.py).
"""

from __future__ import annotations

import html
import os
from urllib.parse import quote


def _setting(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


APP_NAME = _setting("CYBERLOGIX_APP_NAME", "Kryozyn")
APP_SHORT = _setting("CYBERLOGIX_APP_SHORT_NAME", APP_NAME)
COMPANY_NAME = _setting("CYBERLOGIX_COMPANY_NAME", "MTRX Horizyn")
COMPANY_DOMAIN = _setting("CYBERLOGIX_COMPANY_DOMAIN", "mtrxhorizyn.com").lower()
APP_HOST = _setting("CYBERLOGIX_APP_HOST", "kryozyn." + COMPANY_DOMAIN).lower()
CHECKLIST_APP = _setting("CYBERLOGIX_CHECKLIST_APP", "Aegizyn")
APP_URL = "https://" + APP_HOST
STAGING_HOST = "staging." + APP_HOST
CONTACT_EMAIL = (os.environ.get("CYBERLOGIX_SUPPORT_EMAIL") or "").strip()
# The product's name as one token, for the User-Agent of requests it makes.
UA_NAME = "".join(APP_SHORT.split()) or "app"

# The wordmark in a page's header: a two-word name has its last word set
# apart ("Name <em>AI</em>"); one word is the word. Markup, from APP_NAME.
_head, _sep, _tail = APP_NAME.rpartition(" ")
WORDMARK_HTML = (f"{html.escape(_head)} <em>{html.escape(_tail)}</em>" if _sep
                 else html.escape(APP_NAME))

# The tokens a page template uses for the names, and what they become.
TOKENS = {
    "%%APP_NAME%%": html.escape(APP_NAME),
    "%%APP_NAME_URL%%": quote(APP_NAME),
    "%%APP_SHORT%%": html.escape(APP_SHORT),
    "%%APP_WORDMARK%%": WORDMARK_HTML,
    "%%COMPANY_NAME%%": html.escape(COMPANY_NAME),
    "%%APP_HOST%%": html.escape(APP_HOST),
    "%%CHECKLIST_APP%%": html.escape(CHECKLIST_APP),
    "%%CHECKLIST_APP_LOWER%%": html.escape(CHECKLIST_APP.lower()),
}

# Read by the pages' own scripts (brand() in each), so a script never
# spells a name: <meta name="brand" content="APP_NAME" data-short=...>.
BRAND_META = (f'<meta name="brand" content="{html.escape(APP_NAME)}" '
              f'data-short="{html.escape(APP_SHORT)}" '
              f'data-company="{html.escape(COMPANY_NAME)}" '
              f'data-checklist="{html.escape(CHECKLIST_APP)}">')


def fill(page: str) -> str:
    """A page template with the names put in.

    Every token above is replaced, and the brand meta goes in just before
    </head> for the page's scripts to read.
    """
    for token, value in TOKENS.items():
        page = page.replace(token, value)
    if "</head>" in page and 'name="brand"' not in page:
        page = page.replace("</head>", BRAND_META + "\n</head>", 1)
    return page


def facts() -> dict:
    """The names, for Pilot's script tag (main.pilot_facts)."""
    return {"app-name": APP_NAME, "app-short": APP_SHORT, "company-name": COMPANY_NAME,
            "checklist-app": CHECKLIST_APP}
