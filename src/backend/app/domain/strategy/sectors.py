"""One name per sector, whatever the source called it — pure.

The max-3-per-sector cap compares the sector of a kept holding with the sector
of a new candidate. Candidates carry NSE's own industry label (from the index
constituent file); a holding carries whatever was in the ledger when it was
bought. Those did not match: the ledger held "Auto Components", "Auto" and
"Industrial Machinery" for companies NSE files under "Automobile and Auto
Components" and "Capital Goods" — so the cap counted them as different sectors
and never bound.

`canonical_sector` maps any label onto NSE's 22 industry names. An exact NSE
name is returned as is; anything else is matched by keyword; a label that
matches nothing is returned unchanged, so an unknown sector is still a sector
of its own rather than being lumped in with the others.
"""

from __future__ import annotations

NSE_SECTORS: tuple[str, ...] = (
    "Automobile and Auto Components", "Capital Goods", "Chemicals", "Construction",
    "Construction Materials", "Consumer Durables", "Consumer Services", "Diversified",
    "Fast Moving Consumer Goods", "Financial Services", "Forest Materials", "Healthcare",
    "Information Technology", "Media Entertainment & Publication", "Metals & Mining",
    "Oil Gas & Consumable Fuels", "Power", "Realty", "Services", "Telecommunication",
    "Textiles", "Utilities",
)
_BY_NAME = {name.casefold(): name for name in NSE_SECTORS}

# Whole-label shorthands that are too short to match safely as substrings.
_EXACT = {"it": "Information Technology", "fmcg": "Fast Moving Consumer Goods"}

# Checked in order, first hit wins — so the more specific sector comes first
# ("Industrial Automation" is Capital Goods, not Automobile; "Construction
# Materials" is not Construction; "IT Services" is not Services).
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Capital Goods", ("capital good", "machinery", "industrial", "engineering",
                       "electrical equipment", "defence", "aerospace")),
    ("Automobile and Auto Components", ("auto", "tyre", "vehicle")),
    ("Financial Services", ("bank", "financ", "nbfc", "insurance", "broking",
                            "asset management")),
    ("Healthcare", ("health", "pharma", "hospital", "diagnostic", "biotech")),
    ("Information Technology", ("information technology", "software", "it services",
                                "it -")),
    ("Fast Moving Consumer Goods", ("fast moving", "food", "beverage", "personal care")),
    ("Consumer Durables", ("consumer durable", "appliance")),
    ("Chemicals", ("chemical", "fertili")),
    ("Construction Materials", ("cement", "construction material")),
    ("Construction", ("construction", "infrastructure")),
    ("Metals & Mining", ("metal", "mining", "steel")),
    ("Oil Gas & Consumable Fuels", ("oil", "gas", "petrol", "refiner", "coal")),
    ("Power", ("power",)),
    ("Realty", ("realty", "real estate")),
    ("Telecommunication", ("telecom",)),
    ("Textiles", ("textile", "apparel")),
    ("Media Entertainment & Publication", ("media", "entertainment", "publication")),
    ("Consumer Services", ("consumer service", "retail", "hotel", "restaurant")),
    ("Utilities", ("utilit",)),
    ("Forest Materials", ("forest", "paper")),
    ("Services", ("services", "logistic", "shipping")),
    ("Diversified", ("diversified",)),
)


def canonical_sector(label: str | None) -> str:
    """The NSE industry a sector label belongs to (or the label, if unknown)."""
    text = str(label or "").strip()
    low = text.casefold()
    if not low or low == "nan":
        return ""
    if low in _BY_NAME:
        return _BY_NAME[low]
    if low in _EXACT:
        return _EXACT[low]
    for name, keys in _KEYWORDS:
        if any(key in low for key in keys):
            return name
    return text
