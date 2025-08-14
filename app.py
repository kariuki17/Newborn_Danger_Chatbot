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
# Shapely 2.x import fix
from shapely.geometry import Point
from shapely.base import BaseGeometry

# ------------------------------
# 1) Domain data
# ------------------------------

danger_signs: Dict[str, Dict[str, str]] = {
    "fever": {
        "advice": "Your baby has a fever. In newborns this can be serious. Seek medical attention immediately.",
        "urgency": "urgent",
        "synonyms": ["hot body", "high temperature", "temperature"]
    },
    "breathing difficulty": {
        "advice": "Difficulty breathing can be an emergency. Seek emergency care now.",
        "urgency": "emergency",
        "synonyms": ["trouble breathing", "hard to breathe", "respiratory distress", "grunting"]
    },
    "blue lips": {
        "advice": "Bluish lips/tongue may signal poor oxygen levels and possible heart or lung issues. Go to emergency care.",
        "urgency": "emergency",
        "synonyms": ["cyanosis", "bluish lips", "purple lips", "blue tongue"]
    },
    "poor feeding": {
        "advice": "Poor feeding or weak sucking can be concerning. Seek clinical evaluation today.",
        "urgency": "urgent",
        "synonyms": ["not feeding", "refusing feeds", "weak suck"]
    },
}

CARDIAC_KEYWORDS = {"blue", "cyanosis", "murmur", "sweating while feeding", "sweaty", "poor feeding", "fast breathing", "breathing difficulty", "heart", "chest retractions"}

SPECIALTY_FACILITIES = [
    {"facility_name": "Kenyatta National Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Aga Khan University Hospital Nairobi", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
    {"facility_name": "Gertrude's Children's Hospital", "county": "Nairobi", "has_pediatric_cardiologist": True, "has_chd_facilities": True},
]

# ------------------------------
# 2) Helper functions
# ------------------------------

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

ALL_LABELS = []
for label, meta in danger_signs.items():
    ALL_LABELS.append(label)
    ALL_LABELS.extend(meta.get("synonyms", []))

def classify_symptoms(user_text: str) -> Tuple[List[str], str, List[str]]:
    text = normalize(user_text)
    found, messages = [], []

    parts = re.split(r",| and | & |;", text)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        match, score, _ = process.extractOne(p, ALL_LABELS, scorer=fuzz.WRatio)
        if score >= 80:
            key_label = match if match in danger_signs else next((lbl for lbl, meta in danger_signs.items() if match in meta.get("synonyms", [])), None)
            if key_label and key_label not in found:
                found.append(key_label)
                messages.append(danger_signs[key_label]["advice"])

    order = {"advice": 0, "monitor": 1, "urgent": 2, "emergency": 3}
    worst = max((danger_signs[l]["urgency"] for l in found), key=lambda x: order.get(x, 0), default="advice")
    return found, worst, messages

def looks_cardiac(matched_labels: List[str], raw_text: str) -> bool:
    raw = normalize(raw_text)
    return any(lbl in CARDIAC_KEYWORDS for lbl in matched_labels) or any(kw in raw for kw in CARDIAC_KEYWORDS)

def fuzzy_specialty_lookup(name: str) -> Tuple[bool, bool]:
    rec = next((r for r in SPECIALTY_FACILITIES if normalize(r["facility_name"]) == normalize(name)), None)
    if rec:
        return rec["has_pediatric_cardiologist"], rec["has_chd_facilities"]
    return False, False

# ------------------------------
# 3) GIS functions
# ------------------------------

def geocode_place(place: str) -> Optional[Tuple[float, float]]:
    geolocator = Nominatim(user_agent="newborn_danger_chatbot")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(place)
    if not loc:
        return None
    return (loc.latitude, loc.longitude)

def build_map(lat: float, lon: float, radius_km: int = 10):
    tags = {"amenity": "hospital"}
    gdf = ox.geometries_from_point((lat, lon), tags=tags, dist=radius_km*1000)

    m = folium.Map(location=[lat, lon], zoom_start=12)
    folium.Marker([lat, lon], popup="Your Location", icon=folium.Icon(color="blue")).add_to(m)

    for _, row in gdf.iterrows():
        name = row.get("name") or "Unnamed Hospital"
        coords = (row.geometry.centroid.y, row.geometry.centroid.x) if row.geometry else None
        if not coords:
            continue
        has_ped, has_chd = fuzzy_specialty_lookup(name)
        color = "green" if has_ped or has_chd else "orange"
        badge = ", ".join([x for x, flag in [("Pediatric Cardiology", has_ped), ("CHD Facilities", has_chd)] if flag]) or "General hospital"
        popup_html = f"<b>{name}</b><br/>Services: {badge}"
        folium.Marker(coords, popup=popup_html, icon=folium.Icon(color=color)).add_to(m)

    return m

# ------------------------------
# 4) Streamlit UI
# ------------------------------

st.set_page_config(page_title="Newborn Danger Chatbot (Kenya)", page_icon="🍼", layout="wide")
st.title("🍼 Newborn Danger Chatbot — Kenya")
st.caption("Educational aid — not a substitute for professional medical care. Seek care immediately if concerned.")

with st.expander("How it works"):
    st.markdown("""
    - Enter symptoms in everyday language (e.g., "blue lips, sweating while feeding").
    - App flags danger signs and suggests urgency.
    - For urgent cases, you can search nearby hospitals and optionally filter for pediatric cardiology/CHD capability.
    """)

symptoms = st.text_input("Describe the baby's symptoms:", placeholder="e.g., blue lips, sweating while feeding")
check_btn = st.button("Check symptoms")

if check_btn and symptoms.strip():
    matched, worst, adv_msgs = classify_symptoms(symptoms)
    if not matched:
        st.info("Couldn't confidently match danger signs. Seek care if concerned.")
    else:
        st.subheader("Assessment")
        st.write("**Matched danger signs:**", ", ".join(matched))
        st.write("**Urgency:**", f"`{worst.upper()}`")
        for m in adv_msgs:
            st.write("- ", m)

        cardiac_flag = looks_cardiac(matched, symptoms)
        st.markdown("---")
        st.subheader("Find nearby hospitals")
        place = st.text_input("Enter your location (town/estate/landmark)", value="Nairobi, Kenya")
        radius_km = st.slider("Search radius (km)", 1, 50, 15)

        if st.button("Search hospitals"):
            coords = geocode_place(place)
            if not coords:
                st.error("Couldn't find that place. Try a nearby landmark or add county, e.g., 'Kahawa West, Nairobi'.")
            else:
                lat, lon = coords
                with st.spinner("Querying OpenStreetMap and building map..."):
                    try:
                        fmap = build_map(lat, lon, radius_km)
                        st_folium(fmap, width=1000, height=560)
                        st.caption("Green = Pediatric Cardiology / CHD. Orange = general hospital.")
                    except Exception as e:
                        st.error(f"Error while building the map: {e}")
