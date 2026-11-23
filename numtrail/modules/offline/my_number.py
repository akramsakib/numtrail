"""Malaysia numbering plan: decode a +60 number without touching the network.

What this answers, deterministically:

* is this a plausible Malaysian number at all (length, structure)
* mobile or fixed line
* which 01X block it sits in, and which operator that block was allocated to
* how many subscriber digits to expect, and the canonical formats

What it does not answer: whose number it is. Mobile number portability since
2008 means the *active* carrier can differ from the block owner, so the operator
here is the allocation, labelled as such.

Block assignments follow the MCMC national numbering plan as summarised by
public numbering references; where sources disagree the answer says "shared"
instead of guessing. Offline, so it is identical on every deployment — including
a static GitHub Pages build running in the browser.
"""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module

# national significant number prefix (the digits after +60, starting "1") ->
# (block owner, subscriber digits, certainty)
MOBILE_BLOCKS: dict[str, tuple[str, int, str]] = {
    "12": ("Maxis", 7, "allocated"),
    "17": ("Maxis", 7, "allocated"),
    "13": ("Celcom", 7, "allocated"),
    "19": ("Celcom", 7, "allocated"),
    "16": ("Digi", 7, "allocated"),
    "18": ("U Mobile", 7, "allocated"),
    "10": ("Celcom / XOX / MVNOs", 7, "shared"),
    "14": ("unifi Mobile / MVNOs", 7, "shared"),
    "15": ("MVNOs (Tune Talk, XOX, redONE…)", 7, "shared"),
    "11": ("all operators (universal block, 2010)", 8, "shared"),
}

# area code -> where, for the fixed-line ranges
FIXED_AREAS: dict[str, str] = {
    "3": "Kuala Lumpur / Selangor",
    "4": "Penang / Kedah / Perlis",
    "5": "Perak",
    "6": "Melaka / Negeri Sembilan / Johor (north)",
    "7": "Johor",
    "82": "Sarawak (Kuching)",
    "83": "Sarawak (Sri Aman)",
    "84": "Sarawak (Sibu)",
    "85": "Sarawak (Miri / Limbang)",
    "86": "Sarawak (Bintulu)",
    "87": "Sabah (interior)",
    "88": "Sabah (Kota Kinabalu)",
    "89": "Sabah (Sandakan / Tawau)",
    "9": "Kelantan / Terengganu / Pahang",
}


def decode(national: str) -> tuple[Status, str]:
    """Decode the digits after the +60 country code. Pure function, no I/O."""
    nsn = national.lstrip("0")

    if not nsn.isdigit():
        return Status.NOT_FOUND, "not a numeric national number"

    if nsn.startswith("1"):
        if nsn.startswith("11"):
            if len(nsn) != 10:
                return Status.NOT_FOUND, (
                    f"011 block needs 10 NSN digits, got {len(nsn)} "
                    "(e.g. 011-1234 5678 → +601112345678)"
                )
            owner, subscriber, certainty = MOBILE_BLOCKS["11"]
            return Status.FOUND, (
                f"mobile · 011 universal block · {subscriber}-digit subscriber · "
                f"allocated to {owner} · MNP applies"
            )

        block = nsn[:2]
        owner, subscriber, certainty = MOBILE_BLOCKS.get(
            block, ("unrecognised 01X block", 7, "unknown")
        )
        expected = 1 + 1 + subscriber   # "1" + block digit + subscriber
        if len(nsn) != expected:
            return Status.NOT_FOUND, (
                f"0{block} block expects {expected} NSN digits, got {len(nsn)}"
            )
        if certainty == "unknown":
            return Status.NOT_FOUND, f"01x block 0{block} is not in the Malaysian plan"
        qualifier = " (portable — active carrier may differ)" if certainty == "allocated" else ""
        return Status.FOUND, (
            f"mobile · 0{block} block · {subscriber}-digit subscriber · "
            f"allocated to {owner}{qualifier}"
        )

    # fixed line: area code is 1 digit for peninsular states, 2 for 08x
    area = nsn[:2] if nsn.startswith("8") and len(nsn) > 2 else nsn[:1]
    where = FIXED_AREAS.get(area)
    if where is None:
        return Status.NOT_FOUND, f"0{nsn[:1]} is not an allocated Malaysian area code"
    if not 7 <= len(nsn) <= 9:
        return Status.NOT_FOUND, f"fixed-line NSN of {len(nsn)} digits is implausible"
    return Status.FOUND, f"fixed line · area 0{area} ({where})"


def formats(national: str) -> dict[str, str]:
    nsn = national.lstrip("0")
    local = "0" + nsn
    if nsn.startswith("11") and len(nsn) == 10:
        pretty = f"{local[:3]}-{local[3:7]} {local[7:]}"
    elif len(local) == 10:
        pretty = f"{local[:3]}-{local[3:6]} {local[6:]}"
    else:
        pretty = f"{local[:2]}-{local[2:5]} {local[5:]}"
    return {
        "e164": f"+60{nsn}",
        "local": pretty,
        "wa_me": f"https://wa.me/60{nsn}",
    }


@module(
    name="my_number",
    domain="mcmc.gov.my",
    method="offline",
    semantic="number_valid",
    tier="offline",
    tags=("offline", "my", "numbering"),
    summary="Malaysian numbering plan: line type, 01X block, formats (no network)",
)
async def my_number(ctx: CheckContext):
    if ctx.phone.dial != "60":
        return Status.SKIPPED, f"Malaysian numbering only (this is +{ctx.phone.dial})"

    status, detail = decode(ctx.phone.national)
    if status is Status.FOUND:
        f = formats(ctx.phone.national)
        extra = f" · {f['local']}"
        return status, detail + extra
    return status, detail
