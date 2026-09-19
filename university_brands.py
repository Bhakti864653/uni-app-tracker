"""Static, factual university-identity data used only for visual presentation
(brand colors, a real-world location line, a lightweight campus-silhouette
variant). This is never application-tracking data - it doesn't touch the
database and carries no per-user state. Universities not listed here get a
plain initials-based fallback crest, which must always render correctly.
"""

import re

# Real, publicly documented brand colors and one-line identity facts for the
# schools most likely to appear (the 3 named in the redesign brief, plus the
# ones already seeded into every fresh demo account - see seed_demo_data() in
# app.py). Keys are normalized with normalize_university_name().
UNIVERSITY_BRANDS = {
    "northeastern university": {
        "initials": "NU",
        "logo": "assets/university-logos/northeastern-seal.png",
        "logo_attribution": "Northeastern University seal, CC BY-SA 4.0, via Wikimedia Commons",
        "primary": "#C8102E",
        "secondary": "#1C1C1C",
        "location": "Boston, MA",
        "identity": "Co-op & experiential learning",
        "silhouette": "skyline",
    },
    "boston university": {
        "initials": "BU",
        "logo": "assets/university-logos/boston-university-seal.svg",
        "logo_attribution": "Boston University seal, public domain, via Wikimedia Commons",
        "primary": "#CC0000",
        "secondary": "#1C1C1C",
        "location": "Boston, MA",
        "identity": "Charles River campus",
        "silhouette": "river",
    },
    "umass amherst": {
        "initials": "UM",
        "logo": "assets/university-logos/umass-amherst-seal.png",
        "logo_attribution": "University of Massachusetts Amherst seal, public domain, via Wikimedia Commons",
        "primary": "#881C1C",
        "secondary": "#1C1C1C",
        "location": "Amherst, MA",
        "identity": "Flagship campus, college town",
        "silhouette": "tower",
    },
    "stanford university": {
        "initials": "SU",
        "primary": "#8C1515",
        "secondary": "#4D4F53",
        "location": "Stanford, CA",
        "identity": "Research university",
        "silhouette": "trees",
    },
    "university of michigan": {
        "initials": "UM",
        "primary": "#00274C",
        "secondary": "#FFCB05",
        "location": "Ann Arbor, MI",
        "identity": "Big Ten, flagship campus",
        "silhouette": "tower",
    },
    "nyu": {
        "initials": "NYU",
        "primary": "#57068C",
        "secondary": "#1C1C1C",
        "location": "New York, NY",
        "identity": "Urban campus",
        "silhouette": "skyline",
    },
}

# Alternate spellings a student might reasonably type when adding a school.
UNIVERSITY_ALIASES = {
    "university of massachusetts amherst": "umass amherst",
    "umass": "umass amherst",
    "new york university": "nyu",
    "u michigan": "university of michigan",
    "university of michigan ann arbor": "university of michigan",
}

FALLBACK_PALETTE = [
    {"primary": "#3F5B4C", "secondary": "#1C1C1C"},  # forest green
    {"primary": "#6B2737", "secondary": "#1C1C1C"},  # muted burgundy
    {"primary": "#1E3A5F", "secondary": "#1C1C1C"},  # deep navy-blue
    {"primary": "#8A6D3B", "secondary": "#1C1C1C"},  # soft gold-brown
]

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_university_name(name):
    cleaned = _WHITESPACE_RE.sub(" ", (name or "").strip().lower())
    cleaned = re.sub(r"^(the)\s+", "", cleaned)
    return cleaned


def _initials_from_name(name):
    words = [w for w in re.split(r"\s+", (name or "").strip()) if w]
    skip = {"of", "the", "and", "at", "in"}
    letters = [w[0].upper() for w in words if w.lower() not in skip]
    if not letters:
        return "?"
    return "".join(letters[:3])


def get_university_brand(name):
    """Returns brand info for any university name. Never returns None - an
    unmapped name gets a deterministic fallback crest (same name always
    yields the same fallback color, so a school looks consistent across
    page loads without needing a stored preference)."""
    key = normalize_university_name(name)
    key = UNIVERSITY_ALIASES.get(key, key)
    brand = UNIVERSITY_BRANDS.get(key)
    if brand:
        return {**brand, "known": True}

    palette_index = sum(ord(ch) for ch in key) % len(FALLBACK_PALETTE) if key else 0
    fallback = FALLBACK_PALETTE[palette_index]
    return {
        "initials": _initials_from_name(name),
        "primary": fallback["primary"],
        "secondary": fallback["secondary"],
        "location": None,
        "identity": None,
        "silhouette": "generic",
        "known": False,
    }
