"""
Streamlit SEO Geo-Grid Visibility Tracker

Automatically use:
- Google Business Profile Name
- Website URL

to fetch:
- Local Pack & Organic SERP rankings via Serpstack
- Google Maps listing rank via Google Places API

Display:
- Local Pack coverage (Plotly scattermap)
- Organic coverage (Folium heatmap)
- Maps listing coverage (Plotly scattermap)

Requires:
- Google Maps API Key
- Serpstack API Key

Run:
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
from urllib.parse import urlparse
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
import plotly.graph_objects as go

# --- HTTP retry session ---
session = requests.Session()
from requests.adapters import HTTPAdapter, Retry
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# --- Serpstack helpers ---
def serpstack_location_api(api_key, city_query):
    url = 'https://api.serpstack.com/locations'
    params = {'access_key': api_key, 'query': city_query, 'limit': 1}
    try:
        r = session.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0].get('canonical_name')
    except:
        pass
    return None

def serpstack_search(api_key, query, location_name):
    url = 'https://api.serpstack.com/search'
    params = {
        'access_key': api_key,
        'query': query,
        'location': location_name,
        'output': 'json',
        'type': 'web',
        'num': 10,
        'page': 1,
        'auto_location': 0
    }
    try:
        r = session.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get('success', True):
            return data
    except:
        pass
    return {}

# --- Google Places helpers ---
def google_places_fetch(lat, lon, keyword, api_key):
    base = 'https://maps.googleapis.com/maps/api/place/nearbysearch/json'
    params = {'location': f"{lat},{lon}", 'keyword': keyword, 'rankby': 'distance', 'key': api_key}
    all_results = []
    for _ in range(3):
        r = session.get(base, params=params, timeout=30)
        if not r.ok:
            break
        data = r.json()
        all_results.extend(data.get('results', []))
        token = data.get('next_page_token')
        if not token:
            break
        time.sleep(2)
        params['pagetoken'] = token
    structured = []
    for p in all_results:
        structured.append({
            'place_id': p.get('place_id'),
            'name': p.get('name','').lower(),
            'rating': float(p.get('rating') or 0),
            'reviews': int(p.get('user_ratings_total') or 0)
        })
    structured.sort(key=lambda x: (-x['rating'], -x['reviews'], x['name']))
    return structured

def google_places_rank(lat, lon, business_name, domain, api_key):
    spots = google_places_fetch(lat, lon, business_name, api_key)
    for idx, item in enumerate(spots, start=1):
        if business_name.lower() in item['name']:
            return idx
    return None

# --- Color map builder ---
def _build_colormap():
    from branca.colormap import LinearColormap
    return LinearColormap(['red','orange','green'], vmin=0, vmax=1)

# --- Plotly scattermap ---
def create_scattermap(df, center_lat, center_lon, rank_col, title):
    fig = go.Figure()
    for _, row in df.iterrows():
        r = row.get(rank_col)
        if pd.isna(r) or r is None:
            color, text = 'red','X'
        else:
            r = int(r)
            if r <= 3:
                color = 'green'
            elif r <= 10:
                color = 'orange'
            else:
                color = 'red'
            text = str(r)
        fig.add_trace(go.Scattermapbox(
            lat=[row['lat']], lon=[row['lng']], mode='markers+text',
            marker=dict(size=20, color=color), text=[text], textposition='middle center',
            textfont=dict(size=14, color='white'), hoverinfo='text',
            hovertext=(f"Keyword: {row['keyword']}<br>Rank: {text}<br>"
                       f"Dist: {row['dist_km']:.2f}km"),
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

# --- GeoGridTracker ---
class GeoGridTracker:
    def __init__(self, serp_key, gmaps_key):
        self.serpkey = serp_key
        self.gmaps_key = gmaps_key
        self.gmaps = googlemaps.Client(key=gmaps_key)
        self.results = []

    def geocode(self, addr):
        res = self.gmaps.geocode(addr)
        return res[0]['geometry']['location'] if res else None

    def reverse_city(self, lat, lng):
        resp = self.gmaps.reverse_geocode((lat, lng), result_type=['locality','administrative_area_level_1'])
        for comp in resp[0]['address_components']:
            if 'locality' in comp['types'] or 'administrative_area_level_1' in comp['types']:
                return comp['long_name']
        return None

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

    def normalize_url(self, url):
        return url.rstrip('/').lower()

    def run_scan(self, business, website, radius, step, shape, progress=None):
        center = self.geocode(business)
        if not center:
            return []
        target_url = self.normalize_url(website)
        domain = urlparse(website).netloc.lower()
        grid = self.gen_grid(center['lat'], center['lng'], radius, step, shape)
        total = len(grid)
        out = []
        for idx, pt in enumerate(grid, start=1):
            if progress:
                progress.progress(idx / total)
            city = self.reverse_city(pt['lat'], pt['lng']) or ''
            locn = serpstack_location_api(self.serpkey, city)
            serp = serpstack_search(self.serpkey, business, locn or city)
            # Organic: match exact URL first, then fallback to domain/title
            org_rank = None
            for item in serp.get('organic_results', []):
                url = item.get('url', '').rstrip('/').lower()
                pos = item.get('position') or None
                if url == target_url:
                    org_rank = pos
                    break
            if org_rank is None:
                for item in serp.get('organic_results', []):
                    u = item.get('url', '').lower()
                    title = item.get('title', '').lower()
                    if domain in u or business.lower() in title:
                        org_rank = item.get('position')
                        break
            # Local Pack: direct from local_results
            lp_rank = None
            for item in serp.get('local_results', []):
                if business.lower() == item.get('title', '').lower():
                    lp_rank = item.get('position') or None
                    break
            # Google Maps listing
            gmp_rank = google_places_rank(pt['lat'], pt['lng'], business, domain, self.gmaps_key)
            out.append({
                'keyword': business,
                'lat': pt['lat'], 'lng': pt['lng'], 'dist_km': pt['dist_km'],
                'org_rank': org_rank,
                'lp_rank': lp_rank,
                'gmp_rank': gmp_rank
            })
            time.sleep(1)
        self.results = out
        st.session_state['center'] = center
        return out

# --- Streamlit UI ---
st.set_page_config(page_title='SEO Geo-Grid Tracker', layout='wide')
st.title('🌐 SEO Geo-Grid Visibility Tracker')
# Sidebar
gmaps_key = st.sidebar.text_input('Google Maps API Key', type='password')
serp_key = st.sidebar.text_input('Serpstack API Key', type='password')
business = st.sidebar.text_input('Business Profile Name')
website = st.sidebar.text_input('Website URL')
shape = st.sidebar.selectbox('Grid Shape', ['Circle', 'Square'])
radius = st.sidebar.slider('Radius (km)', 0.5, 10.0, 2.0, 0.5)
step = st.sidebar.slider('Spacing (km)', 0.1, 2.0, 0.5, 0.1)
tracker = GeoGridTracker(serp_key, gmaps_key) if serp_key and gmaps_key else None
if tracker and business and website:
    if st.sidebar.button('Run Scan'):
        prog = st.progress(0)
        data = tracker.run_scan(business, website, radius, step, shape, prog)
        st.session_state['data'] = data
        df = pd.DataFrame(data)
        st.session_state['summary'] = {
            'total': len(df),
            'org_pct': df['org_rank'].notna().mean() * 100,
            'lp_pct': df['lp_rank'].notna().mean() * 100,
            'gmp_pct': df['gmp_rank'].notna().mean() * 100
        }
else:
    st.sidebar.warning('Enter all fields to run scan')

# Display results
if 'data' in st.session_state:
    s = st.session_state['summary']
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Points', s['total'])
    c2.metric('Organic %', f"{s['org_pct']:.1f}%")
    c3.metric('Local Pack %', f"{s['lp_pct']:.1f}%")
    c4.metric('Maps %', f"{s['gmp_pct']:.1f}%")
    tab1, tab2, tab3 = st.tabs(['Local Pack Coverage', 'Organic Heatmap', 'Maps Coverage'])
    center = st.session_state['center']
    df = pd.DataFrame(st.session_state['data'])
    with tab1:
        fig1 = create_scattermap(df, center['lat'], center['lng'], 'lp_rank', 'Local Pack Coverage')
        st.plotly_chart(fig1, use_container_width=True)
    with tab2:
        folmap = folium.Map(location=[center['lat'], center['lng']], zoom_start=12, tiles='CartoDB positron')
        HeatMap([(d['lat'], d['lng'], (11 - d['org_rank'] if d['org_rank'] and d['org_rank'] <= 10 else 0)) for d in st.session_state['data']]).add_to(folmap)
        st_folium(folmap, key='organic_heatmap', width=700, height=500)
    with tab3:
        fig3 = create_scattermap(df, center['lat'], center['lng'], 'gmp_rank', 'Google Maps Coverage')
        st.plotly_chart(fig3, use_container_width=True)
    # Downloads
    st.download_button('Download CSV', df.to_csv(index=False), 'results.csv', key='csv')
    st.download_button('Download JSON', json.dumps(st.session_state['data']), 'results.json', key='json')

st.markdown('---')
st.write('© Built with Serpstack & Google Maps API')
