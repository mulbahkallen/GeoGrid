"""
Streamlit SEO Geo-Grid Visibility Tracker

A web interface for tracking local search engine visibility across a geographic grid.
Requires:
- Google Maps API key
- SerpAPI key (for Google search results)

Run with: streamlit run streamlit_geo_grid_tracker.py
"""

import os
import time
import json
import math
import datetime
import requests
import pandas as pd
import numpy as np
import streamlit as st
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
import plotly.graph_objects as go


class GeoGridTracker:
    def __init__(self, google_maps_api_key, serp_api_key):
        self.google_maps_api_key = google_maps_api_key
        self.serp_api_key = serp_api_key
        self.results_data = []

    def geocode_address(self, address):
        """Geocode an address to get latitude and longitude."""
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": self.google_maps_api_key}
        response = requests.get(url, params=params)
        data = response.json()
        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]
        # Log status and error_message for debugging
        st.error(f"Geocode API returned status '{data.get('status')}' with message: {data.get('error_message', 'No message')}.")
        return None

    def generate_grid(self, center_lat, center_lng, radius_km, spacing_km, shape="Circle"):
        grid_points = []
        lat_deg = radius_km / 111.0
        lng_deg = radius_km / (111.0 * math.cos(math.radians(center_lat)))
        lat_steps = int(2 * lat_deg / (spacing_km / 111.0)) + 1
        lng_steps = int(2 * lng_deg / (spacing_km / (111.0 * math.cos(math.radians(center_lat))))) + 1
        for i in range(lat_steps):
            for j in range(lng_steps):
                lat_offset = (i * (2 * lat_deg / (lat_steps - 1))) if lat_steps > 1 else 0
                lng_offset = (j * (2 * lng_deg / (lng_steps - 1))) if lng_steps > 1 else 0
                point_lat = center_lat - lat_deg + lat_offset
                point_lng = center_lng - lng_deg + lng_offset
                distance = geodesic((center_lat, center_lng), (point_lat, point_lng)).kilometers
                if shape == "Circle":
                    if distance <= radius_km:
                        grid_points.append({"lat": point_lat, "lng": point_lng, "distance_km": distance})
                else:
                    grid_points.append({"lat": point_lat, "lng": point_lng, "distance_km": distance})
        return grid_points

    def get_place_details(self, business_name, location):
        """Get details about a business from Google Places API."""
        url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        params = {
            "input": business_name,
            "inputtype": "textquery",
            "fields": "place_id,name,formatted_address,geometry",
            "locationbias": f"point:{location['lat']},{location['lng']}",
            "key": self.google_maps_api_key
        }
        response = requests.get(url, params=params)
        data = response.json()
        if data.get("status") == "OK" and data.get("candidates"):
            return data["candidates"][0]
        return None

    def search_serp(self, keyword, location, language="en", country="us"):
        """Search Google SERP for a keyword at a specific location."""
        url = "https://serpapi.com/search"
        params = {
            "q": keyword,
            "location": f"{location['lat']},{location['lng']}",
            "hl": language,
            "gl": country,
            "api_key": self.serp_api_key
        }
        try:
            response = requests.get(url, params=params)
            return response.json()
        except Exception as e:
            st.error(f"Error searching SERP: {e}")
            return None

    def find_business_rank(self, serp_data, business_name):
        """Find the rank of a business in SERP results."""
        organic_rank = None
        if "organic_results" in serp_data:
            for i, res in enumerate(serp_data["organic_results"]):
                if business_name.lower() in res.get("title", "").lower():
                    organic_rank = i + 1
                    break
        local_pack_rank = None
        if "local_results" in serp_data:
            for i, res in enumerate(serp_data["local_results"]):
                if business_name.lower() in res.get("title", "").lower():
                    local_pack_rank = i + 1
                    break
        return {
            "organic_rank": organic_rank,
            "local_pack_rank": local_pack_rank,
            "is_in_organic": organic_rank is not None,
            "is_in_local_pack": local_pack_rank is not None
        }

    def run_geo_grid_tracking(self, business_name, center_lat, center_lng,
                              radius_km, spacing_km, keywords,
                              shape="Circle", progress_bar=None):
        """Run geo-grid tracking for a business across keywords."""
        grid = self.generate_grid(center_lat, center_lng, radius_km, spacing_km, shape)
        results = []
        total = len(grid) * len(keywords)
        count = 0
        for pt in grid:
            for kw in keywords:
                if progress_bar:
                    count += 1
                    progress_bar.progress(count / total)
                data = self.search_serp(kw, pt)
                if not data:
                    continue
                rd = self.find_business_rank(data, business_name)
                results.append({
                    "business_name": business_name,
                    "keyword": kw,
                    "lat": pt["lat"],
                    "lng": pt["lng"],
                    "distance_km": pt["distance_km"],
                    "timestamp": datetime.datetime.now().isoformat(),
                    **rd
                })
                time.sleep(1)
        self.results_data = results
        return results

    def create_folium_map(self, data, center_lat, center_lng, result_type="local_pack_rank"):
        """Create a Folium map with the results."""
        m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
        for pt in data:
            rank = pt.get(result_type)
            if rank is not None:
                norm = 11 - rank if rank <= 10 else 0
                folium.CircleMarker(
                    location=[pt["lat"], pt["lng"]],
                    radius=8,
                    color='blue' if result_type == 'local_pack_rank' else 'green',
                    fill=True,
                    fill_color=self._get_color_by_rank(norm),
                    fill_opacity=0.7,
                    popup=f"Keyword: {pt['keyword']}<br>Organic: {pt['organic_rank']}<br>Local: {pt['local_pack_rank']}"
                ).add_to(m)
        heat = [[pt["lat"], pt["lng"], (11-pt[result_type])/10] for pt in data if pt.get(result_type) is not None]
        if heat:
            HeatMap(heat).add_to(m)
        return m

    def _get_color_by_rank(self, norm):
        if norm >= 0.8:
            return 'green'
        if norm >= 0.5:
            return 'yellow'
        if norm > 0:
            return 'orange'
        return 'red'

    def generate_summary_data(self):
        if not self.results_data:
            return None
        df = pd.DataFrame(self.results_data)
        total = len(df)
        org = df['is_in_organic'].sum()
        loc = df['is_in_local_pack'].sum()
        summary = {
            'business_name': df['business_name'].iloc[0],
            'total_queries': total,
            'organic_presence_pct': org/total*100,
            'local_presence_pct': loc/total*100
        }
        if org:
            summary['avg_organic_rank'] = df.loc[df['organic_rank'].notna(),'organic_rank'].mean()
        if loc:
            summary['avg_local_rank'] = df.loc[df['local_pack_rank'].notna(),'local_pack_rank'].mean()
        # further stats omitted for brevity
        return {'summary': summary}


# Streamlit App UI
st.set_page_config(page_title="SEO Geo-Grid Visibility Tracker", page_icon="🌐", layout="wide")

st.title("🌐 SEO Geo-Grid Visibility Tracker")
st.markdown("Track local search visibility across a geographic grid. Enter business profile name + address to center the grid.")

# Sidebar
st.sidebar.header("Configuration")
google_api_key = st.sidebar.text_input("Google Maps API Key", type="password")
serp_api_key = st.sidebar.text_input("SerpAPI Key", type="password")

st.sidebar.header("Business Details")
business_profile_name = st.sidebar.text_input("Business Profile Name", "")
# Add placeholder showing full address format
business_address = st.sidebar.text_input(
    "Business Address",
    "",
    placeholder="1600 Amphitheatre Parkway, Mountain View, CA 94043, USA"
)


st.sidebar.header("Grid Options")
grid_shape = st.sidebar.selectbox("Grid Shape", ["Circle", "Square"])
radius_km = st.sidebar.slider("Radius (km)", 0.5, 10.0, 2.0, 0.5)
spacing_km = st.sidebar.slider("Grid Spacing (km)", 0.1, 2.0, 0.5, 0.1)

st.sidebar.header("Keywords")
keywords = [k.strip() for k in st.sidebar.text_area("Keywords (one per line)", "coffee shop near me" "espresso bar café").split("\n") if k.strip()]

# Session state
if 'results' not in st.session_state:
    st.session_state.results = None
if 'summary_data' not in st.session_state:
    st.session_state.summary_data = None

# Grid preview function
def display_grid_preview():
    if not business_address or not google_api_key:
        st.warning("Please enter your business address and Google Maps API key")
        return
    tracker = GeoGridTracker(google_api_key, serp_api_key)
    place = tracker.geocode_address(business_address)
    if not place:
        st.error("Unable to geocode address")
        return
    loc = place['geometry']['location']
    pts = tracker.generate_grid(loc['lat'], loc['lng'], radius_km, spacing_km, grid_shape)
    m = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=12)
    folium.Marker(location=[loc['lat'], loc['lng']], popup="Center", icon=folium.Icon(color='red')).add_to(m)
    if grid_shape == "Circle":
        folium.Circle(location=[loc['lat'], loc['lng']], radius=radius_km*1000,
                      color='red', fill=True, fill_opacity=0.1).add_to(m)
    for p in pts:
        folium.CircleMarker(location=[p['lat'], p['lng']], radius=3,
                            color='blue', fill=True, fill_opacity=0.5).add_to(m)
    st_folium(m, height=400, width=700)
    st.info(f"Generated {len(pts)} grid points ({grid_shape})")

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["Setup & Run", "Results Maps", "Analytics", "Raw Data"])  
with tab1:
    st.header("Grid Preview")
    display_grid_preview()

    st.header("Run Tracking")
    if st.button("Start Tracking", disabled=not (google_api_key and serp_api_key and business_profile_name and business_address and keywords)):
        tracker = GeoGridTracker(google_api_key, serp_api_key)
        place = tracker.geocode_address(business_address)
        if not place:
            st.error("Unable to geocode address")
        else:
            loc = place['geometry']['location']
            bar = st.progress(0, text="Running geo-grid tracking...")
            results = tracker.run_geo_grid_tracking(
                business_profile_name, loc['lat'], loc['lng'], radius_km, spacing_km, keywords, shape=grid_shape, progress_bar=bar
            )
            st.session_state.results = results
            st.session_state.summary_data = tracker.generate_summary_data()
            st.session_state.center = loc
            bar.progress(1.0, text="Tracking complete!")
            st.success(f"✅ {len(results)} data points collected.")

with tab2:
    if st.session_state.results:
        st.header("Rankings Maps")
        result_type = st.radio("Result Type", ["local_pack_rank", "organic_rank"], horizontal=True,
                               format_func=lambda x: "Local Pack" if x=="local_pack_rank" else "Organic")
        for kw in keywords:
            st.subheader(f"Keyword: {kw}")
            md = [r for r in st.session_state.results if r['keyword']==kw]
            tracker = GeoGridTracker(google_api_key, serp_api_key)
            mp = tracker.create_folium_map(md, st.session_state.center['lat'], st.session_state.center['lng'], result_type)
            st_folium(mp, height=500, width=800)
        st.markdown("""
        ### Map Legend
        - **Green**: Top (1-3)
        - **Yellow**: Medium (4-6)
        - **Orange**: Lower (7-10)
        - **Red**: Not ranked/ >10
        - **Heatmap**: Intensity = better visibility
        """)
    else:
        st.info("Run tracking to see results")

with tab3:
    if st.session_state.summary_data:
        sum = st.session_state.summary_data['summary']
        st.header("SEO Visibility Summary")
        c1,c2,c3 = st.columns(3)
        c1.metric("Queries", sum['total_queries'])
        c2.metric("Organic %", f"{sum['organic_presence_pct']:.1f}%")
        c3.metric("Local %", f"{sum['local_presence_pct']:.1f}%")
    else:
        st.info("Run tracking to see analytics")

with tab4:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        st.dataframe(df)
        st.download_button("CSV", data=df.to_csv(index=False), file_name="results.csv", mime="text/csv")
    else:
        st.info("Run tracking to see raw data")

st.markdown("---")
st.markdown("SEO Geo-Grid Visibility Tracker | Build local SEO strategies with location-based ranking data")
