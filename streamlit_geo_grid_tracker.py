"""
Streamlit SEO Geo-Grid Visibility Tracker

A tool to automatically derive keywords and geolocation from:
- Google Business Profile Name
- Business Website URL

Tracks:
- Local Pack rankings (Plotly scattermap)
- Organic SERP rankings (Folium heatmap)

Requires:
- Google Maps API Key
- ScraperAPI Access Key

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

# --- Build colormap for Folium ---
def _build_colormap():
    from branca.colormap import LinearColormap
    return LinearColormap(['red','orange','green'], vmin=0, vmax=1)

# --- Plotly scattermap ---
def create_scattermap(df, center_lat, center_lon, rank_col, title):
    fig = go.Figure()
    for _, row in df.iterrows():
        r = row[rank_col]
        if pd.isna(r) or r > 10:
            color, text = 'red','X'
        elif r <=3:
            color, text = 'green', str(int(r))
        else:
            color, text = 'orange', str(int(r))
        fig.add_trace(go.Scattermapbox(
            lat=[row['lat']], lon=[row['lng']],
            mode='markers+text',
            marker=dict(size=20,color=color),
            text=[text], textposition='middle center', textfont=dict(size=14,color='white'),
            hoverinfo='text',
            hovertext=f"KW: {row['keyword']}<br>Rank: {text}<br>Dist: {row['dist_km']:.2f} km",
            showlegend=False
        ))
    fig.update_layout(
        mapbox_style='open-street-map',
        mapbox_center={'lat':center_lat,'lon':center_lon},
        mapbox_zoom=12,
        margin=dict(r=0,t=30,l=0,b=0),
        title=title
    )
    return fig

# --- Core Tracker ---
class GeoGridTracker:
    def __init__(self, scraper_key, gmaps_key):
        self.scraper_key = scraper_key
        self.gmaps = googlemaps.Client(key=gmaps_key)
        self.results = []

    def find_place(self, business_name):
        resp = self.gmaps.find_place(
            input=business_name, input_type='textquery',
            fields=['place_id','geometry','name'],
            location_bias=None
        )
        cand = resp.get('candidates',[])
        if not cand:
            st.error("Business not found on Google Maps.")
            return None
        return cand[0]

    def extract_keywords(self, place_id, business_name):
        details = self.gmaps.place(place_id=place_id, fields=['types'])
        types = details.get('result',{}).get('types',[])
        # convert types like 'cafe' to 'cafe near me'
        keywords = [business_name] + [t.replace('_',' ') for t in types]
        return keywords[:10]  # limit for performance

    def gen_grid(self, lat0, lng0, radius, step, shape):
        pts=[]
        lat_deg = radius/111.0
        lng_deg = radius/(111.0*math.cos(math.radians(lat0)))
        rows = int(2*lat_deg/(step/111.0))+1
        cols = int(2*lng_deg/(step/111.0))+1
        for i in range(rows):
            for j in range(cols):
                lat = lat0 - lat_deg + (i*2*lat_deg/(rows-1) if rows>1 else 0)
                lng = lng0 - lng_deg + (j*2*lng_deg/(cols-1) if cols>1 else 0)
                d = geodesic((lat0,lng0),(lat,lng)).km
                if shape=='Circle' and d>radius: continue
                pts.append({'lat':lat,'lng':lng,'dist_km':d})
        return pts

    def search_serp(self, keyword, lat, lng):
        url = 'https://api.scraperapi.com/structured/google/search'
        params = {'api_key': self.scraper_key,
                  'query': keyword,
                  'location':f"{lat},{lng}" }
        r = requests.get(url, params=params)
        return r.json() if r.ok else {}

    def run_scan(self, business_name, website_url, radius, step, shape, progress=None):
        # 1) locate business
        place = self.find_place(business_name)
        if not place: return []
        place_id = place['place_id']
        loc0 = place['geometry']['location']
        # 2) derive keywords
        keywords = self.extract_keywords(place_id, business_name)
        # 3) derive domain
        domain = urlparse(website_url).netloc.lower()
        # 4) grid
        grid = self.gen_grid(loc0['lat'],loc0['lng'],radius,step,shape)
        total = len(grid)*len(keywords)
        out=[]; cnt=0
        for pt in grid:
            for kw in keywords:
                cnt+=1
                if progress: progress.progress(cnt/total)
                serp = self.search_serp(kw,pt['lat'],pt['lng'])
                # organic: match domain first, then name
                org = next((i for i,r in enumerate(serp.get('organic_results',[]),1)
                            if domain in r.get('url','') or business_name.lower() in r.get('title','').lower()),None)
                # map pack: match title or place_id
                lp = next((i for i,r in enumerate(serp.get('local_results',[]),1)
                           if business_name.lower() in r.get('title','').lower()),None)
                out.append({'keyword':kw,
                            'lat':pt['lat'],'lng':pt['lng'],'dist_km':pt['dist_km'],
                            'org_rank':org,'lp_rank':lp})
                time.sleep(0.5)
        self.results = out
        return out

# --- Streamlit UI ---
st.set_page_config(page_title="SEO Geo-Grid Tracker",layout="wide")
st.title("🌐 SEO Geo-Grid Visibility Tracker")

# Sidebar
gmaps_key = st.sidebar.text_input("Google Maps API Key",type='password')
scraper_key = st.sidebar.text_input("ScraperAPI Key",type='password')
business_name = st.sidebar.text_input("Google Business Profile Name")
website_url = st.sidebar.text_input("Business Website URL (e.g. https://example.com)")
shape = st.sidebar.selectbox("Grid Shape",['Circle','Square'])
radius = st.sidebar.slider("Radius (km)",0.5,10.0,2.0,0.5)
step = st.sidebar.slider("Spacing (km)",0.1,2.0,0.5,0.1)

# Action
tracker = GeoGridTracker(scraper_key, gmaps_key) if scraper_key and gmaps_key else None
if tracker and business_name and website_url:
    if st.sidebar.button("Run Scan"):
        prog=st.progress(0)
        data = tracker.run_scan(business_name,website_url,radius,step,shape,prog)
        st.session_state['data'] = data
        s = pd.DataFrame(data)
        st.session_state['summary'] = {
            'total':len(s),
            'org_pct':s['org_rank'].notna().mean()*100,
            'lp_pct':s['lp_rank'].notna().mean()*100
        }
else:
    st.sidebar.warning("Enter credentials, business name & website URL.")

# Display
if 'data' in st.session_state:
    s = st.session_state['summary']
    c1,c2,c3=st.columns(3)
    c1.metric("Points",s['total'])
    c2.metric("Organic %",f"{s['org_pct']:.1f}%")
    c3.metric("Local Pack %",f"{s['lp_pct']:.1f}%")

    tab1,tab2=st.tabs(["Local Pack Coverage","Organic Heatmap"])
    # Local Pack
    with tab1:
        df=pd.DataFrame(st.session_state['data'])
        loc0 = tracker.gmaps.find_place(input=business_name,input_type='textquery',fields=['geometry'])['candidates'][0]['geometry']['location']
        fig = create_scattermap(df,loc0['lat'],loc0['lng'],'lp_rank','Local Pack Coverage')
        st.plotly_chart(fig,use_container_width=True)
    # Organic
    with tab2:
        df=st.session_state['data']
        loc0 = tracker.gmaps.find_place(input=business_name,input_type='textquery',fields=['geometry'])['candidates'][0]['geometry']['location']
        folmap = folium.Map(location=[loc0['lat'],loc0['lng']],zoom_start=12,tiles='CartoDB positron')
        HeatMap([(d['lat'],d['lng'],(11-d['org_rank'] if d['org_rank'] and d['org_rank']<=10 else 0)) for d in df]).add_to(folmap)
        st_folium(folmap,key='organic_heatmap')
    # Downloads
    df_full=pd.DataFrame(st.session_state['data'])
    st.download_button("Download CSV",df_full.to_csv(index=False),"results.csv",key="csv")
    st.download_button("Download JSON",json.dumps(st.session_state['data']),"results.json",key="json")

st.markdown("---")
st.write("© Built with ScraperAPI & Google Maps API")
