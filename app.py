# app.py — Newborn Danger Chatbot + GIS hospital finder (Kenya-focused)
# ---------------------------------------------------------------
# Features
# - Simple newborn danger-sign checker (rule-based)
# - If urgency is high, optionally find nearby hospitals from OpenStreetMap
# - Overlay specialty info (pediatric cardiology / CHD facilities) using a small curated list
# - Interactive map via Folium inside Streamlit
# ---------------------------------------------------------------

import streamlit as st
import re
from typing import Dict, List, Tuple, Optional

# GIS + data tools
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import osmnx as ox
import folium
from streamlit_folium import st_folium
from rapidfuzz import process, fuzz
from shapely.geometry.base import BaseGeometry
from shapely.geometry import Point

# ------------------------------
# 1) Domain data
# ------------------------------

# Minimal example. Extend freely with your latest version.
danger_signs: Dict[str, Dict[str, str]] = {
    "fever": {
        "advice": (
            "Your baby has a fever. In newborns this can be serious. Keep the baby lightly dressed, "
            "avoid cold baths, and seek medical attention immediately."
        ),
        "urgency": "urgent",
        "synonyms": ["hot body", "high temperature", "temperature"]
    },
    "breathing difficulty": {
        "advice": (
            "Difficulty breathing can be an emergency. If you notice fast breathing, chest retractions, "
            "grunting, or pauses in breathing, seek emergency care now."
        ),
        "urgency": "emergency",
        "synonyms": ["trouble breathing", "hard to breathe", "respiratory distress", "grunting"]
    },
    "blue lips": {
        "advice": (
            "Bluish lips/tongue (cyanosis) may signal poor oxygen levels and possible heart or lung issues. "
            "Keep the baby warm and go to the nearest emergency facility immediately."
        ),
        "urgency": "emergency",
        "synonyms": ["cyanosis", "bluish lips", "purple lips", "blue tongue"]
    },
    "poor feeding": {
        "advice": (
            "Poor feeding, refusing feeds, or weak sucking can be concerning in a newborn. Try to feed "
            "small amounts frequently and seek clinical evaluation today."
        ),
        "urgency": "urgent",
        "synonyms": ["not feeding", "refusing feeds", "weak suck"]
    },
    "sweating while feeding": {
        "advice": (
            "Excessive sweating while feeding can be a sign of a heart problem in infants. "
            "Reduce feeding duration, allow rests, and seek urgent assessment."
        ),
        "urgency": "urgent",
        "synonyms": ["sweats during feeding", "sweaty when feeding", "sweaty feeds"]
    },
    "fast breathing": {
        "advice": (
            "Fast breathing (tachypnea) can indicate infection or heart/lung issues. If persistent, seek "
            "emergency care."
        ),
        "urgency": "emergency",
        "synonyms": ["rapid breathing", "breathing fast", "tachypnea"]
    },
    "chest retractions": {
        "advice": (
            "Chest retractions (skin pulling in between ribs) while breathing is a danger sign. Go to the "
            "nearest emergency department now."
        ),
        "urgency": "emergency",
        "synonyms": ["subcostal retractions", "intercostal retractions", "ribs showing when breathing"]
    },
}

# Keywords that lean cardiac/CHD to prioritize cardiology-capable centers
CARDIAC_KEYWORDS = {
    "blue", "cyanosis", "murmur", "sweating while feeding", "sweaty", "poor feeding",
    "fast breathing", "breathing difficulty", "heart", "chest retractions"
}

# Curated specialty list (seed; expand this file as you verify). County names should match normal usage.
SPECIALTY_FACILITIES = [
    {"facility_name": "Kenyatta National Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Aga Khan University Hospital Nairobi", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Gertrude's Children's Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "The Karen Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Mater Misericordiae Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Tenwek Hospital", "county": "Bomet", "has_pediatric_cardiologist": False, "has_chd_facilities": True},
    {"facility_name": "Moi Teaching and Referral Hospital (MTRH)", "county": "Uasin Gishu", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Shoe4Africa Children's Hospital", "county": "Uasin Gishu", "has_pediatric_cardiologist": False, "has_chd_facilities": False},
]

# ------------------------------
# 2) Helper functions — NLP-ish matching
# ------------------------------

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

# Build a flat list of labels for fuzzy matching
ALL_LABELS = []
for label, meta in danger_signs.items():
    ALL_LABELS.append(label)
    for syn in meta.get("synonyms", []):
        ALL_LABELS.append(syn)


def classify_symptoms(user_text: str) -> Tuple[List[str], str, List[str]]:
    """
    Return (matched_labels, worst_urgency, messages)
    worst_urgency can be: "advice", "monitor", "urgent", "emergency" (by our simple scale)
    """
    text = normalize(user_text)

    found: List[str] = []
    messages: List[str] = []

    # Split by commas/and to allow multiple
    parts = re.split(r",| and | & |;", text)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        match, score, _ = process.extractOne(p, ALL_LABELS, scorer=fuzz.WRatio)
        if score >= 80:  # threshold — tune if needed
            # Map synonym back to key label if needed
            key_label = None
            if match in danger_signs:
                key_label = match
            else:
                # find which label owns this synonym
                for lbl, meta in danger_signs.items():
                    if match in meta.get("synonyms", []):
                        key_label = lbl
                        break
            if key_label and key_label not in found:
                found.append(key_label)
                messages.append(danger_signs[key_label]["advice"])

    # Compute worst urgency
    order = {"advice": 0, "monitor": 1, "urgent": 2, "emergency": 3}
    worst = "advice"
    for lbl in found:
        u = danger_signs[lbl]["urgency"]
        if order.get(u, 0) > order.get(worst, 0):
            worst = u

    return found, worst, messages


def looks_cardiac(matched_labels: List[str], raw_text: str) -> bool:
    raw = normalize(raw_text)
    if any(lbl in CARDIAC_KEYWORDS for lbl in matched_labels):
        return True
    return any(kw in raw for kw in CARDIAC_KEYWORDS)

# ------------------------------
# 3) GIS functions — geocode, OSM query, matching
# ------------------------------

def geocode_place(place: str) -> Optional[Tuple[float, float]]:
    geolocator = Nominatim(user_agent="newborn_danger_chatbot_ke")
    # add a modest rate limiter for courtesy
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(place)
    if not loc:
        return None
    return (loc.latitude, loc.longitude)


def geometries_from_point(lat: float, lon: float, radius_km: int = 10):
    # Query OSM for hospitals (amenity=hospital OR healthcare=hospital)
    tags = {"amenity": "hospital"}
    gdf = ox.geometries_from_point((lat, lon), tags=tags, dist=radius_km * 1000)
    # Healthcare=hospital sometimes present on different features
    try:
        gdf2 = ox.geometries_from_point((lat, lon), tags={"healthcare": "hospital"}, dist=radius_km * 1000)
        gdf = (
            gdf2 if gdf is None or gdf.empty else gdf2 if gdf2 is not None and gdf2.shape[0] > gdf.shape[0] else gdf
        )
        if gdf2 is not None and not gdf2.empty:
            gdf = ox.utils_geo.concat_gdfs([gdf, gdf2]).drop_duplicates()
    except Exception:
        pass
    return gdf


def geometry_to_latlon(geom: BaseGeometry) -> Optional[Tuple[float, float]]:
    if geom is None:
        return None
    if isinstance(geom, Point):
        return (geom.y, geom.x)
    try:
        c = geom.centroid
        return (c.y, c.x)
    except Exception:
        return None


def fuzzy_specialty_lookup(name: str, default_county: Optional[str] = None) -> Tuple[bool, bool, Optional[str], int]:
    """Return (has_ped_card, has_chd, matched_name, score). Optionally bias by county in tie-breaks."""
    if not name:
        return False, False, None, 0

    choices = [row["facility_name"] for row in SPECIALTY_FACILITIES]
    match, score, _ = process.extractOne(name, choices, scorer=fuzz.WRatio)

    if score < 85:
        return False, False, None, score

    # Pull the record
    rec = next((r for r in SPECIALTY_FACILITIES if r["facility_name"] == match), None)
    if not rec:
        return False, False, match, score

    # If county is provided and mismatched, slightly penalize (simple heuristic)
    if default_county and rec.get("county") and normalize(default_county) != normalize(rec["county"]):
        # reduce confidence but still return
        score = max(0, score - 5)

    return bool(rec["has_pediatric_cardiologist"]), bool(rec["has_chd_facilities"]), match, score


def build_map(lat: float, lon: float, radius_km: int = 10, only_specialty: bool = False):
    gdf = geometries_from_point(lat, lon, radius_km)

    m = folium.Map(location=[lat, lon], zoom_start=12)
    folium.Marker([lat, lon], popup="Your Location", icon=folium.Icon(color="blue")).add_to(m)

    if gdf is None or gdf.empty:
        folium.Circle([lat, lon], radius=radius_km * 1000, popup="Search area").add_to(m)
        return m, 0, 0

    total = 0
    shown = 0

    for _, row in gdf.iterrows():
        total += 1
        name = row.get("name") or row.get("official_name") or "Unnamed Hospital"
        county = row.get("addr:county") or row.get("is_in:county")
        coords = geometry_to_latlon(row.geometry)
        if not coords:
            continue

        has_ped, has_chd, matched_name, score = fuzzy_specialty_lookup(str(name), default_county=county)

        if only_specialty and not (has_ped or has_chd):
            continue

        marker_color = "green" if (has_ped or has_chd) else "orange"
        badge = []
        if has_ped:
            badge.append("Pediatric Cardiology")
        if has_chd:
            badge.append("CHD Facilities")
        badge_str = ", ".join(badge) if badge else "General hospital"

        popup_html = f"""
        <b>{name}</b><br/>
        {('Matched: ' + matched_name + ' (score ' + str(score) + ')<br/>' ) if matched_name else ''}
        County: {county or '—'}<br/>
        Services: {badge_str}
        """
        folium.Marker(
            [coords[0], coords[1]],
            popup=folium.Popup(popup_html, max_width=350),
            icon=folium.Icon(color=marker_color, icon="plus")
        ).add_to(m)
        shown += 1

    folium.Circle([lat, lon], radius=radius_km * 1000, fill=False, weight=1).add_to(m)
    return m, total, shown

# ------------------------------
# 4) Streamlit UI
# ------------------------------

st.set_page_config(page_title="Newborn Danger Chatbot (Kenya)", page_icon="🍼", layout="wide")

st.title("🍼 Newborn Danger Chatbot — Kenya")
st.caption("Educational aid — not a substitute for professional medical care. If in doubt, seek care immediately.")

with st.expander("How it works"):
    st.markdown(
        """
        - Enter symptoms in everyday language (e.g., *"blue lips, sweating while feeding"*).
        - The app flags danger signs and suggests urgency.
        - For urgent/emergency cases, you can search nearby hospitals and optionally filter for **pediatric cardiology/CHD** capability.
        """
    )

symptoms = st.text_input("Describe the baby's symptoms:", placeholder="e.g., blue lips, sweating while feeding, breathing fast")
colA, colB = st.columns([1,1])
with colA:
    check_btn = st.button("Check symptoms")

result_area = st.container()
map_area = st.container()

if check_btn and symptoms.strip():
    matched, worst, adv_msgs = classify_symptoms(symptoms)

    if not matched:
        result_area.info("I couldn't confidently match danger signs. If you're concerned, please seek care.")
    else:
        st.subheader("Assessment")
        st.write("**Matched danger signs:** ", ", ".join(matched))
        st.write("**Urgency:** ", f"`{worst.upper()}`")
        for m in adv_msgs:
            st.write("- ", m)

        cardiac_flag = looks_cardiac(matched, symptoms)
        st.markdown("---")
        st.subheader("Find nearby hospitals")
        st.caption("Powered by OpenStreetMap. Location is only used in your browser session.")

        place = st.text_input("Enter your location (town/estate/landmark)", value="Nairobi, Kenya")
        radius_km = st.slider("Search radius (km)", 1, 50, 15)
        only_specialty = st.checkbox("Show only hospitals with Pediatric Cardiology / CHD services", value=cardiac_flag)

        if st.button("Search hospitals"):
            coords = geocode_place(place)
            if not coords:
                st.error("Couldn't find that place. Try a nearby landmark or add county, e.g., 'Kahawa West, Nairobi'.")
            else:
                lat, lon = coords
                with st.spinner("Querying OpenStreetMap and building map..."):
                    try:
                        fmap, total, shown = build_map(lat, lon, radius_km=radius_km, only_specialty=only_specialty)
                        st.success(f"Found {shown} of {total} hospitals in {radius_km} km.")
                        st_folium(fmap, width=1000, height=560)
                        st.caption("Green = has pediatric cardiology and/or CHD facilities (from curated list). Orange = general hospital.")
                    except Exception as e:
                        st.error(f"Error while building the map: {e}")

# ------------------------------
# 5) Footer / Notes
# ------------------------------
with st.expander("Data & Limitations"):
    st.markdown(
        """
        - Hospital locations are sourced live from **OpenStreetMap** around your search area.
        - Pediatric cardiology/CHD status comes from a small **curated list** embedded in this app — please expand/verify over time.
        - Names in OpenStreetMap may differ from official names; we use fuzzy matching which can be imperfect.
        - Always call ahead to confirm availability of pediatric cardiology services before traveling.
        """
    )
