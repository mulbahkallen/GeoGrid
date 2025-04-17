"""
Streamlit SEO Geo-Grid Visibility Tracker

A web interface for tracking local search engine visibility across a geographic grid.
Requires:
- Google Maps API key
- Serpstack Access Key (for Google SERP data)

Run with: streamlit run streamlit_geo_grid_tracker.py
"""

import time
import json
import math
import datetime
import requests
import pandas as pd
import streamlit as st
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
import plotly.graph_objects as go


class GeoGridTracker:
    def __init__(self, google_maps_api_key: str, serpstack_access_key: str):
        self.google_maps_api_key = google_maps_api_key
        self.serpstack_access_key = serpstack_access_key
        self.results_data = []

    def geocode_address(self, address: str):
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": self.google_maps_api_key}
        resp = requests.get(url, params=params)
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]
        st.error(f"Geocode API returned status '{data.get('status')}' with message: {data.get('error_message','No message')}." )
        return None

    def generate_grid(self, center_lat: float, center_lng: float,
                      radius_km: float, spacing_km: float,
                      shape: str = "Circle") -> list:
        pts = []
        lat_deg = radius_km / 111.0
        lng_deg = radius_km / (111.0 * math.cos(math.radians(center_lat)))
        lat_steps = int(2 * lat_deg / (spacing_km / 111.0)) + 1
        lng_steps = int(2 * lng_deg / (spacing_km / (111.0 * math.cos(math.radians(center_lat))))) + 1
        for i in range(lat_steps):
            for j in range(lng_steps):
                lat_off = (i * (2 * lat_deg / (lat_steps - 1))) if lat_steps > 1 else 0
                lng_off = (j * (2 * lng_deg / (lng_steps - 1))) if lng_steps > 1 else 0
                lat = center_lat - lat_deg + lat_off
                lng = center_lng - lng_deg + lng_off
                dist = geodesic((center_lat, center_lng), (lat, lng)).kilometers
                if shape == "Circle" and dist > radius_km:
                    continue
                pts.append({"lat": lat, "lng": lng, "distance_km": dist})
        return pts

    def search_serp(self, keyword: str, location: dict,
                    language: str = "en", country: str = "us") -> dict:
        url = "https://api.serpstack.com/search"
        params = {
            "access_key": self.serpstack_access_key,
            "query": keyword,
            "location": f"{location['lat']},{location['lng']}",
            "engine": "google",
            "device": "desktop",
            "gl": country,
            "hl": language,
            "num": 10
        }
        resp = requests.get(url, params=params)
        data = resp.json()
        if not data.get("success", True):
            err = data.get("error", {})
            st.error(f"Serpstack error {err.get('code')}: {err.get('info')}")
            return {}
        return data

    def find_business_rank(self, serp_data: dict, business_name: str) -> dict:
        org = None
        for i, r in enumerate(serp_data.get("organic_results", []), start=1):
            if business_name.lower() in r.get("title", "").lower():
                org = i
                break
        loc = None
        for i, r in enumerate(serp_data.get("local_results", []), start=1):
            if business_name.lower() in r.get("title", "").lower():
                loc = i
                break
        return {
            "organic_rank": org,
            "local_pack_rank": loc,
            "is_in_organic": org is not None,
            "is_in_local_pack": loc is not None
        }

    def run_geo_grid_tracking(self, business_name: str,
                              center_lat: float, center_lng: float,
                              radius_km: float, spacing_km: float,
                              keywords: list, shape: str = "Circle",
                              progress_bar=None) -> list:
        points = self.generate_grid(center_lat, center_lng, radius_km, spacing_km, shape)
        total = len(points) * len(keywords)
        count = 0
        out = []
        for pt in points:
            for kw in keywords:
                if progress_bar:
                    count += 1
                    progress_bar.progress(count / total)
                data = self.search_serp(kw, pt)
                if not data:
                    continue
                ranks = self.find_business_rank(data, business_name)
                rec = {
                    "business_name": business_name,
                    "keyword": kw,
                    "lat": pt["lat"],
                    "lng": pt["lng"],
                    "distance_km": pt["distance_km"],
                    "timestamp": datetime.datetime.now().isoformat(),
                    **ranks
                }
                out.append(rec)
                time.sleep(1)
        self.results_data = out
        return out

    def create_folium_map(self, data: list,
                          center_lat: float, center_lng: float,
                          result_type: str = "local_pack_rank") -> folium.Map:
        """Create a Folium map with hoverable circle markers showing rank."""
        m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
        from folium.features import DivIcon
        for pt in data:
            rank = pt.get(result_type)
            lat, lng = pt["lat"], pt["lng"]
            if rank is not None:
                norm = (11 - rank) / 10 if rank <= 10 else 0
                color = self._get_color_by_rank(norm)
                # Draw colored circle with tooltip
                folium.CircleMarker(
                    location=[lat, lng],
                    radius=12,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.7,
                    tooltip=f"{result_type.replace('_',' ').title()}: {rank}"
                ).add_to(m)
                # Overlay rank number
                folium.map.Marker(
                    [lat, lng],
                    icon=DivIcon(
                        html=f'<div style="font-size:10px;color:white;text-align:center;">{rank}</div>'
                    )
                ).add_to(m)
            else:
                # Not ranked: red circle with X
                folium.CircleMarker(
                    location=[lat, lng],
                    radius=12,
                    color='red',
                    fill=True,
                    fill_color='red',
                    fill_opacity=0.7,
                    tooltip="Not ranked (X)"
                ).add_to(m)
                folium.map.Marker(
                    [lat, lng],
                    icon=DivIcon(
                        html='<div style="font-size:10px;color:white;text-align:center;">×</div>'
                    )
                ).add_to(m)
        return m

    def _get_color_by_rank(self, norm: float) -> str:(self, norm: float) -> str:
        if norm >= 0.8:
            return 'green'
        if norm >= 0.5:
            return 'yellow'
        if norm > 0:
            return 'orange'
        return 'red'

    def generate_summary_data(self) -> dict:
        if not self.results_data:
            return {}
        df = pd.DataFrame(self.results_data)
        total = len(df)
        org = df['is_in_organic'].sum()
        loc = df['is_in_local_pack'].sum()
        summary = {
            'business_name': df['business_name'].iloc[0],
            'total_queries': total,
            'organic_presence_pct': org / total * 100,
            'local_presence_pct': loc / total * 100
        }
        if org:
            summary['avg_organic_rank'] = df.loc[df['organic_rank'].notna(), 'organic_rank'].mean()
        if loc:
            summary['avg_local_rank'] = df.loc[df['local_pack_rank'].notna(), 'local_pack_rank'].mean()
        return {'summary': summary}


# Streamlit App
st.set_page_config(page_title="SEO Geo-Grid Visibility Tracker",
                   page_icon="🌐", layout="wide")

st.title("🌐 SEO Geo-Grid Visibility Tracker")
st.markdown("Track local search visibility across a geographic grid.")

# Sidebar inputs
google_api_key = st.sidebar.text_input("Google Maps API Key", type="password")
serpstack_key = st.sidebar.text_input("Serpstack Access Key", type="password")

business_profile_name = st.sidebar.text_input("Business Profile Name", "")
business_address = st.sidebar.text_input(
    "Business Address",
    placeholder="1600 Amphitheatre Parkway, Mountain View, CA 94043, USA"
)

grid_shape = st.sidebar.selectbox("Grid Shape", ["Circle", "Square"])
radius_km = st.sidebar.slider("Radius (km)", 0.5, 10.0, 2.0, 0.5)
spacing_km = st.sidebar.slider("Grid Spacing (km)", 0.1, 2.0, 0.5, 0.1)

keywords = [k.strip() for k in st.sidebar.text_area(
    "Keywords (one per line)", "coffee shop near me\nespresso bar\ncafé"
).split("\n") if k.strip()]

# Session state init
if 'results' not in st.session_state:
    st.session_state['results'] = []
if 'summary' not in st.session_state:
    st.session_state['summary'] = {}

# Grid preview function
def display_grid_preview():
    if not business_address or not google_api_key:
        st.warning("Enter both address and Google Maps key to preview grid.")
        return
    tracker = GeoGridTracker(google_api_key, serpstack_key)
    place = tracker.geocode_address(business_address)
    if not place:
        return
    loc = place['geometry']['location']
    pts = tracker.generate_grid(loc['lat'], loc['lng'], radius_kм, spacing_kм, grid_shape)
    m = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=12)
    folium.Marker([loc['lat'], loc['lng']], popup="Center").add_to(m)
    if grid_shape == "Circle":
        folium.Circle([loc['lat'], loc['lng']], radius=radius_kм*1000,
                      color='red', fill=True, fill_opacity=0.1).add_to(m)
    for p in pts:
        folium.CircleMarker([p['lat'], p['lng']], radius=3,
                            color='blue', fill=True, fill_opacity=0.5).add_to(m)
    st_folium(m, width=700, height=400, key="grid_preview")
    st.info(f"{len(pts)} points generated ({grid_shape}).")

# Main UI tabs
tab1, tab2, tab3, tab4 = st.tabs(["Setup & Run", "Maps", "Summary", "Raw Data"])

with tab1:
    st.header("Grid Preview")
    display_grid_preview()
    st.header("Run Tracking")
    if st.button("Start Tracking", disabled=not all([google_api_key, serpstack_key,
                                                       business_profile_name, business_address, keywords])):
        tracker = GeoGridTracker(google_api_key, serpstack_key)
        place = tracker.geocode_address(business_address)
        if place:
            loc = place['geometry']['location']
            bar = st.progress(0, text="Running tracking...")
            data = tracker.run_geo_grid_tracking(
                business_profile_name,
                loc['lat'], loc['lng'],
                radius_kм, spacing_kм,
                keywords, 
                shape=grid_shape,
                progress_bar=bar
            )
            st.session_state['results'] = data
            st.session_state['summary'] = tracker.generate_summary_data()
            st.success(f"Collected {len(data)} points.")

with tab2:
    if st.session_state['results']:
        choice = st.radio("Result Type", ["local_pack_rank", "organic_rank"], horizontal=True)
        for idx, kw in enumerate(keywords):
            st.subheader(kw)
            subset = [r for r in st.session_state['results'] if r['keyword'] == kw]
            tr = GeoGridTracker(google_api_key, serpstack_key)
            m = tr.create_folium_map(subset,
                                     st.session_state['results'][0]['lat'],
                                     st.session_state['results'][0]['lng'],
                                     choice)
            st_folium(m, width=800, height=500, key=f"map_{idx}")
    else:
        st.info("No results yet. Run tracking first.")

with tab3:
    summary = st.session_state.get('summary', {})
    if summary:
        s = summary['summary']
        st.metric("Total Queries", s['total_queries'])
        st.metric("Organic %", f"{s['organic_presence_pct']:.1f}%")
        st.metric("Local %", f"{s['local_presence_pct']:.1f}%")
    else:
        st.info("Summary will appear here after running tracking.")

with tab4:
    if st.session_state['results']:
        df = pd.DataFrame(st.session_state['results'])
        st.dataframe(df)
        csv = df.to_csv(index=False)
        st.download_button("Download CSV", data=csv, file_name="results.csv")
        st.download_button("Download JSON", data=json.dumps(st.session_state['results']), file_name="results.json")
    else:
        st.info("Run tracking to see raw data.")

st.markdown("---")
st.markdown("Built with Serpstack & Google Maps API")
