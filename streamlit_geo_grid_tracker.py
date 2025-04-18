"""
Streamlit SEO Geo-Grid Visibility Tracker

A comprehensive tool to track:
- Google Local Pack rankings
- Organic SERP rankings
- Google Maps listing rankings
across a customizable geographic grid, with competitor insights, historical comparisons, and exportable reports.

Requires:
- Google Maps API Key
- ScraperAPI Access Key (for Google SERP)

Run the app:
    streamlit run streamlit_geo_grid_tracker.py
"""

import time
import datetime
import json
import math
import requests
import pandas as pd
import streamlit as st
import googlemaps
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap
from folium.map import Marker
from folium.features import DivIcon
from branca.colormap import LinearColormap
from streamlit_folium import st_folium

# --- Helper: build color map ---
def _build_colormap():
    return LinearColormap(['green', 'orange', 'red'], vmin=1, vmax=10)

# --- GeoGrid Tracker Class ---
class GeoGridTracker:
    def __init__(self, gmaps_key: str, scraper_key: str):
        self.gmaps = googlemaps.Client(key=gmaps_key)
        self.scraper_key = scraper_key
        self.results = []

    def geocode(self, address: str):
        res = self.gmaps.geocode(address)
        if not res:
            st.error("Geocoding failed. Check address and API key.")
            return None
        return res[0]['geometry']['location']

    def find_place_id(self, name: str, loc: dict):
        resp = self.gmaps.find_place(
            input=name, input_type='textquery', fields=['place_id'],
            location_bias=f"point:{loc['lat']},{loc['lng']}"
        )
        cand = resp.get('candidates', [])
        return cand[0]['place_id'] if cand else None

    def gen_grid(self, lat0, lng0, radius, step, shape):
        pts = []
        lat_deg = radius / 111.0
        lng_deg = radius / (111.0 * math.cos(math.radians(lat0)))
        rows = int(2 * lat_deg / (step / 111.0)) + 1
        cols = int(2 * lng_deg / (step / 111.0)) + 1
        for i in range(rows):
            for j in range(cols):
                lat = lat0 - lat_deg + (i * 2 * lat_deg / (rows - 1) if rows > 1 else 0)
                lng = lng0 - lng_deg + (j * 2 * lng_deg / (cols - 1) if cols > 1 else 0)
                d = geodesic((lat0, lng0), (lat, lng)).km
                if shape == 'Circle' and d > radius:
                    continue
                pts.append({'lat': lat, 'lng': lng, 'dist_km': d})
        return pts

    def search_serp(self, kw, loc, lang='en', country='us'):
        url = 'https://api.scraperapi.com/structured/google/search'
        params = {'api_key': self.scraper_key, 'query': kw, 'language': lang, 'country': country}
        resp = requests.get(url, params=params)
        return resp.json() if resp.ok else {}

    def search_places(self, kw, loc, radius_m=1000):
        return self.gmaps.places_nearby(location=(loc['lat'], loc['lng']), radius=radius_m, keyword=kw).get('results', [])

    def run(self, business, address, radius, step, keywords, shape, progress=None):
        center = self.geocode(address)
        if not center:
            return []
        target_id = self.find_place_id(business, center)
        grid = self.gen_grid(center['lat'], center['lng'], radius, step, shape)
        total = len(grid) * len(keywords)
        out = []
        count = 0
        for pt in grid:
            for kw in keywords:
                count += 1
                if progress:
                    progress.progress(count / total)
                serp = self.search_serp(kw, pt)
                places = self.search_places(kw, pt)
                org = next((i for i, r in enumerate(serp.get('organic_results', []), 1)
                            if business.lower() in r.get('title', '').lower()), None)
                lp = next((i for i, r in enumerate(serp.get('local_results', []), 1)
                           if business.lower() in r.get('title', '').lower()), None)
                gmp = next((i for i, p in enumerate(places, 1)
                            if p.get('place_id') == target_id), None)
                out.append({
                    'keyword': kw,
                    'lat': pt['lat'], 'lng': pt['lng'], 'dist_km': pt['dist_km'],
                    'org_rank': org, 'lp_rank': lp, 'gmp_rank': gmp,
                    'timestamp': datetime.datetime.now().isoformat()
                })
                time.sleep(0.5)
        self.results = out
        return out

    def summarize(self):
        df = pd.DataFrame(self.results)
        summary = {
            'total_checks': len(df),
            'org_pct': df['org_rank'].notna().mean() * 100,
            'lp_pct': df['lp_rank'].notna().mean() * 100,
            'gmp_pct': df['gmp_rank'].notna().mean() * 100
        }
        for c in ['org_rank', 'lp_rank', 'gmp_rank']:
            if df[c].notna().any():
                summary[f'avg_{c}'] = df[c].dropna().mean()
        return summary

    def map(self, data, center, mode):
        # Google Maps base layer
        m = folium.Map(location=center, zoom_start=13, tiles=None)
        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
            attr='Google', name='Google Roadmap', control=True
        ).add_to(m)
        cmap = _build_colormap()
        cmap.caption = f"{mode.replace('_',' ').title()}"  

        # Add markers with numbers and colors
        for d in data:
            r = d.get(mode)
            if r is None:
                color = 'red'
                text = 'X'
            elif r <= 3:
                color = 'green'
                text = str(r)
            elif r <= 10:
                color = 'orange'
                text = str(r)
            else:
                color = 'red'
                text = 'X'
            html = f"<div style='background:{color};color:white;border-radius:50%;width:24px;height:24px;line-height:24px;text-align:center;font-size:12px'>{text}</div>"
            Marker(
                location=(d['lat'], d['lng']),
                icon=DivIcon(html=html)
            ).add_to(m)

        # Optional heatmap overlay
        hm_data = [(d['lat'], d['lng'], (11 - d[mode] if d.get(mode) and d[mode] <= 10 else 0))
                   for d in data if d.get(mode) is not None]
        HeatMap(hm_data, radius=25, gradient={0:'red',0.5:'orange',1:'green'}).add_to(
            folium.FeatureGroup(name='Heatmap', show=False).add_to(m)
        )

        folium.LayerControl(collapsed=False).add_to(m)
        m.add_child(cmap)
        return m

# --- Streamlit App ---
st.set_page_config(page_title="SEO Geo-Grid Tracker", layout="wide")
st.title("🌐 SEO Geo-Grid Visibility Tracker")

# Sidebar Inputs
gkey = st.sidebar.text_input("Google Maps API Key", type='password')
skey = st.sidebar.text_input("ScraperAPI Key", type='password')
if not gkey or not skey:
    st.sidebar.warning("Enter both API keys.")

biz = st.sidebar.text_input("Business Profile Name")
addr = st.sidebar.text_input("Business Address", "1600 Amphitheatre Parkway, Mountain View, CA")
shape = st.sidebar.selectbox("Grid Shape", ['Circle','Square'])
rad = st.sidebar.slider("Radius (km)", 0.5, 10.0, 2.0, 0.5)
step = st.sidebar.slider("Spacing (km)", 0.1, 2.0, 0.5, 0.1)
keywords = [k.strip() for k in st.sidebar.text_area(
    "Keywords (one per line)", "coffee shop near me\nespresso bar\ncafé"
).split("\n") if k.strip()]

# Instantiate tracker
tracker = None
if gkey and skey:
    tracker = GeoGridTracker(gkey, skey)

# Run Scan
def run_scan():
    progress = st.progress(0)
    data = tracker.run(biz, addr, rad, step, keywords, shape, progress)
    st.session_state['data'] = data
    st.session_state['summary'] = tracker.summarize()

if tracker and st.sidebar.button("Run Scan"):
    run_scan()

# Display Summary
if 'summary' in st.session_state:
    s = st.session_state['summary']
    cols = st.columns(4)
    cols[0].metric("Total Checks", s['total_checks'])
    cols[1].metric("Organic %", f"{s['org_pct']:.1f}%")
    cols[2].metric("Local Pack %", f"{s['lp_pct']:.1f}%")
    cols[3].metric("Maps %", f"{s['gmp_pct']:.1f}%")

# Tabs
tab1, tab2, tab3 = st.tabs(["Organic","Local Pack","Maps"])
for tab, mode in zip([tab1, tab2, tab3], ['org_rank','lp_rank','gmp_rank']):
    with tab:
        data = st.session_state.get('data', [])
        if data:
            if tracker:
                loc = tracker.geocode(addr)
                if loc:
                    center = [loc['lat'], loc['lng']]
                    m = tracker.map(data, center, mode)
                    st_folium(m, width=800, height=600)
                    df = pd.DataFrame(data)
                    st.download_button(
                        label="Download CSV",
                        data=df.to_csv(index=False),
                        file_name=f"results_{mode}.csv",
                        key=f"download_csv_{mode}"
                    )
                    st.download_button(
                        label="Download JSON",
                        data=json.dumps(data, default=str),
                        file_name=f"results_{mode}.json",
                        key=f"download_json_{mode}"
                    )
                else:
                    st.error("Could not geocode address.")
            else:
                st.error("Missing API keys.")
        else:
            st.info("Run a scan to display results.")

st.markdown("---")
st.write("© Built with ScraperAPI & Google Maps API")
