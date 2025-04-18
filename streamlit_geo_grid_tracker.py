"""
Streamlit SEO Geo-Grid Visibility Tracker

A comprehensive tool to track:
- Google Local Pack rankings (Plotly scattermap)
- Organic SERP rankings (Folium heatmap)
across a customizable geographic grid.

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
import numpy as np
import streamlit as st
import googlemaps
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap
from folium.features import DivIcon
from streamlit_folium import st_folium
import plotly.graph_objects as go

# --- Helper: build color map for Folium heatmap legend ---
def _build_colormap():
    from branca.colormap import LinearColormap
    return LinearColormap(['red', 'orange', 'green'], vmin=0, vmax=1)

# --- Plotly scattermap for Local Pack coverage ---
def create_scattermap(df, center_lat, center_lon, rank_col, title):
    fig = go.Figure()
    for _, row in df.iterrows():
        r = row[rank_col]
        if pd.isna(r) or r > 10:
            color = 'red'
            text = 'X'
        elif r <= 3:
            color = 'green'
            text = str(int(r))
        else:
            color = 'orange'
            text = str(int(r))
        fig.add_trace(go.Scattermapbox(
            lat=[row['lat']],
            lon=[row['lng']],
            mode='markers+text',
            marker=dict(size=20, color=color),
            text=[text],
            textposition='middle center',
            textfont=dict(size=14, color='white'),
            hoverinfo='text',
            hovertext=f"KW: {row['keyword']}<br>Rank: {text}<br>Dist: {row['dist_km']:.2f} km",
            showlegend=False
        ))
    fig.update_layout(
        mapbox_style='open-street-map',
        mapbox_center={'lat': center_lat, 'lon': center_lon},
        mapbox_zoom=12,
        margin=dict(r=0, t=30, l=0, b=0),
        title=title
    )
    return fig

# --- GeoGridTracker Class ---
class GeoGridTracker:
    def __init__(self, scraper_key: str):
        self.scraper_key = scraper_key
        self.results = []

    def search_serp(self, keyword, loc, lang='en', country='us'):
        url = 'https://api.scraperapi.com/structured/google/search'
        params = {
            'api_key': self.scraper_key,
            'query': keyword,
            'language': lang,
            'country': country,
            'location': f"{loc['lat']},{loc['lng']}"
        }
        r = requests.get(url, params=params)
        return r.json() if r.ok else {}

    def gen_grid(self, lat0, lng0, radius, step, shape):
        pts = []
        lat_deg = radius / 111.0
        lng_deg = radius / (111.0 * math.cos(math.radians(lat0)))
        steps_lat = int(2 * lat_deg / (step / 111.0)) + 1
        steps_lng = int(2 * lng_deg / (step / 111.0)) + 1
        for i in range(steps_lat):
            for j in range(steps_lng):
                lat = lat0 - lat_deg + (i * 2 * lat_deg / (steps_lat - 1) if steps_lat>1 else 0)
                lng = lng0 - lng_deg + (j * 2 * lng_deg / (steps_lng - 1) if steps_lng>1 else 0)
                d = geodesic((lat0, lng0), (lat, lng)).km
                if shape=='Circle' and d>radius:
                    continue
                pts.append({'lat': lat, 'lng': lng, 'dist_km': d})
        return pts

    def run(self, business_name, address, radius, step, keywords, shape, gmaps_client, progress=None):
        # Geocode center
        geocode_res = gmaps_client.geocode(address)
        if not geocode_res:
            st.error("Unable to geocode address.")
            return []
        loc0 = geocode_res[0]['geometry']['location']

        grid_points = self.gen_grid(loc0['lat'], loc0['lng'], radius, step, shape)
        total = len(grid_points) * len(keywords)
        out = []
        count = 0

        for pt in grid_points:
            for kw in keywords:
                count += 1
                if progress:
                    progress.progress(count/total)
                serp_data = self.search_serp(kw, pt)

                # Organic rank
                org_rank = next((i for i, r in enumerate(serp_data.get('organic_results', []), 1)
                                 if business_name.lower() in r.get('title', '').lower()), None)
                # Local pack rank
                lp_rank = next((i for i, r in enumerate(serp_data.get('local_results', []), 1)
                                if business_name.lower() in r.get('title', '').lower()), None)

                out.append({
                    'keyword': kw,
                    'lat': pt['lat'], 'lng': pt['lng'], 'dist_km': pt['dist_km'],
                    'org_rank': org_rank, 'lp_rank': lp_rank,
                    'timestamp': datetime.datetime.now().isoformat()
                })
                time.sleep(0.5)

        self.results = out
        return out

    def folium_map(self, data, center, rank_col):
        m = folium.Map(location=center, zoom_start=12, tiles='CartoDB positron')
        cmap = _build_colormap()
        cmap.caption = 'Organic Heat'
        hm_data = [(d['lat'], d['lng'], (11 - d[rank_col] if d.get(rank_col) and d[rank_col]<=10 else 0))
                   for d in data]
        HeatMap(hm_data, radius=20, gradient={'0':'red','0.5':'orange','1':'green'}).add_to(m)
        folium.LayerControl().add_to(m)
        return m

# --- Streamlit App ---
st.set_page_config(page_title="SEO Geo-Grid Tracker", layout="wide")
st.title("🌐 SEO Geo-Grid Visibility Tracker")

# Sidebar inputs
gmaps_key = st.sidebar.text_input("Google Maps API Key", type='password')
scraper_key = st.sidebar.text_input("ScraperAPI Key", type='password')
business = st.sidebar.text_input("Business Profile Name")
address = st.sidebar.text_input("Business Address", "1600 Amphitheatre Parkway, Mountain View, CA")
shape = st.sidebar.selectbox("Grid Shape", ['Circle', 'Square'])
radius = st.sidebar.slider("Radius (km)", 0.5, 10.0, 2.0, 0.5)
spacing = st.sidebar.slider("Spacing (km)", 0.1, 2.0, 0.5, 0.1)
keywords = [k.strip() for k in st.sidebar.text_area(
    "Keywords (one per line)", "coffee shop near me\nespresso bar\ncafé").split("\n") if k.strip()]

# Instantiate
tracker = GeoGridTracker(scraper_key) if scraper_key else None
gmaps_client = googlemaps.Client(key=gmaps_key) if gmaps_key else None

# Run
if tracker and gmaps_client and business and address and keywords:
    if st.sidebar.button("Run Scan"):
        prog = st.progress(0)
        data = tracker.run(business, address, radius, spacing, keywords, shape, gmaps_client, prog)
        st.session_state['data'] = data
        st.session_state['summary'] = {
            'total': len(data),
            'org_pct': pd.DataFrame(data)['org_rank'].notna().mean()*100,
            'lp_pct': pd.DataFrame(data)['lp_rank'].notna().mean()*100
        }
else:
    st.sidebar.warning("Enter all credentials and fields to enable scan.")

# Display Summary and Tabs
if 'data' in st.session_state:
    s = st.session_state['summary']
    cols = st.columns(3)
    cols[0].metric("Total", s['total'])
    cols[1].metric("Organic %", f"{s['org_pct']:.1f}%")
    cols[2].metric("Local Pack %", f"{s['lp_pct']:.1f}%")

    tab1, tab2 = st.tabs(["Local Pack Coverage", "Organic Heatmap"])

    # Local Pack Coverage with Plotly
    with tab1:
        df = pd.DataFrame(st.session_state['data'])
        loc0 = gmaps_client.geocode(address)[0]['geometry']['location']
        fig = create_scattermap(df, loc0['lat'], loc0['lng'], 'lp_rank', 'Local Pack Coverage')
        st.plotly_chart(fig, use_container_width=True)

    # Organic Heatmap with Folium
    with tab2:
        df = st.session_state['data']
        loc0 = gmaps_client.geocode(address)[0]['geometry']['location']
        folmap = tracker.folium_map(df, [loc0['lat'], loc0['lng']], 'org_rank')
        st_folium(folmap, key='organic_heatmap')

    # Downloads
    df_full = pd.DataFrame(st.session_state['data'])
    st.download_button("Download CSV", df_full.to_csv(index=False), "results.csv", key="csv_dl")
    st.download_button("Download JSON", json.dumps(st.session_state['data']), "results.json", key="json_dl")

st.markdown("---")
st.write("© Built with ScraperAPI & Google Maps API")
