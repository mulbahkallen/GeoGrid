"""
Streamlit SEO Geo-Grid Visibility Tracker

Automatically use:
- Google Business Profile Name
- Website URL

to fetch:
- Local Pack rankings via Serpstack
- Organic SERP rankings via Serpstack
- Display Local Pack via Plotly and heatmap organic via Folium

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

# --- Global retry session for HTTP calls ---
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

# --- Build colormap for Folium heatmap ---
def _build_colormap():
    from branca.colormap import LinearColormap
    return LinearColormap(['red','orange','green'], vmin=0, vmax=1)

# --- Plotly scattermap for Local Pack ---
def create_scattermap(df, center_lat, center_lon, rank_col, title):
    fig = go.Figure()
    for _, row in df.iterrows():
        r = row[rank_col]
        if pd.isna(r): color, text = 'red', 'X'
        else:
            if r <= 3: color='green'
            elif r <= 10: color='orange'
            else: color='red'
            text = str(int(r))
        fig.add_trace(go.Scattermapbox(
            lat=[row['lat']], lon=[row['lng']], mode='markers+text',
            marker=dict(size=20, color=color), text=[text], textposition='middle center',
            textfont=dict(size=14,color='white'), hoverinfo='text',
            hovertext=f"KW:{row['keyword']}<br>Rank:{text}<br>Dist:{row['dist_km']:.2f}km",
            showlegend=False
        ))
    fig.update_layout(mapbox_style='open-street-map', mapbox_center={'lat':center_lat,'lon':center_lon},
                      mapbox_zoom=12, margin=dict(r=0,t=30,l=0,b=0), title=title)
    return fig

# --- Core Tracker ---
class GeoGridTracker:
    def __init__(self, serp_key, gmaps_key):
        self.serpkey = serp_key
        self.gmaps = googlemaps.Client(key=gmaps_key)
        self.results = []

    def geocode(self, address):
        try:
            res = self.gmaps.geocode(address)
            if res:
                return res[0]['geometry']['location']
        except Exception:
            pass
        st.error('Geocode failed')
        return None

    def reverse_city(self, lat, lng):
        try:
            r = self.gmaps.reverse_geocode((lat,lng), result_type=['locality','administrative_area_level_1'])
            for comp in r[0]['address_components']:
                if 'locality' in comp['types'] or 'administrative_area_level_1' in comp['types']:
                    return comp['long_name']
        except:
            pass
        return None

    def gen_grid(self, lat0, lng0, radius, step, shape):
        pts=[]
        lat_deg=radius/111.0
        lng_deg=radius/(111.0*math.cos(math.radians(lat0)))
        rows=int(2*lat_deg/(step/111.0))+1
        cols=int(2*lng_deg/(step/111.0))+1
        for i in range(rows):
            for j in range(cols):
                lat=lat0-lat_deg+(i*2*lat_deg/(rows-1) if rows>1 else 0)
                lng=lng0-lng_deg+(j*2*lng_deg/(cols-1) if cols>1 else 0)
                d=geodesic((lat0,lng0),(lat,lng)).km
                if shape=='Circle' and d>radius: continue
                pts.append({'lat':lat,'lng':lng,'dist_km':d})
        return pts

    def run_scan(self, business, website, radius, step, shape, progress=None):
        center = self.geocode(business)
        if not center: return []
        domain = urlparse(website).netloc.lower()
        grid = self.gen_grid(center['lat'], center['lng'], radius, step, shape)
        total = len(grid)*1  # single keyword: business
        out=[]; count=0
        for pt in grid:
            count+=1
            if progress: progress.progress(count/total)
            # get city & canonical
            city = self.reverse_city(pt['lat'], pt['lng'])
            loc_name = serpstack_location_api(self.serpkey, city or '')
            serp = serpstack_search(self.serpkey, business, loc_name or city)
            # organic
            org = next((i for i,r in enumerate(serp.get('organic_results',[]),1)
                        if domain in r.get('url','') or business.lower() in r.get('title','').lower()), None)
            # map pack
            lp = next((i for i,r in enumerate(serp.get('local_results',[]),1)
                       if business.lower() in r.get('title','').lower()), None)
            out.append({'keyword':business,'lat':pt['lat'],'lng':pt['lng'],'dist_km':pt['dist_km'],
                        'org_rank':org,'lp_rank':lp})
            time.sleep(1)
        self.results = out
        return out

# --- Streamlit UI ---
st.set_page_config(page_title='SEO Geo-Grid Tracker',layout='wide')
st.title('🌐 SEO Geo-Grid Visibility Tracker')
# Sidebar
gmaps_key=st.sidebar.text_input('Google Maps API Key',type='password')
serp_key=st.sidebar.text_input('Serpstack API Key',type='password')
business=st.sidebar.text_input('Business Profile Name')
website=st.sidebar.text_input('Website URL')
shape=st.sidebar.selectbox('Grid Shape',['Circle','Square'])
radius=st.sidebar.slider('Radius (km)',0.5,10.0,2.0,0.5)
step=st.sidebar.slider('Spacing (km)',0.1,2.0,0.5,0.1)

tracker = GeoGridTracker(serp_key, gmaps_key) if serp_key and gmaps_key else None
if tracker and business and website:
    if st.sidebar.button('Run Scan'):
        prog=st.progress(0)
        data=tracker.run_scan(business,website,radius,step,shape,prog)
        st.session_state['data']=data
        s=pd.DataFrame(data)
        st.session_state['summary']={'total':len(s),'org_pct':s['org_rank'].notna().mean()*100,'lp_pct':s['lp_rank'].notna().mean()*100}
else:
    st.sidebar.warning('Enter all fields')

# Display
if 'data' in st.session_state:
    s=st.session_state['summary']
    c1,c2,c3=st.columns(3)
    c1.metric('Points',s['total'])
    c2.metric('Organic %',f"{s['org_pct']:.1f}%")
    c3.metric('Local Pack %',f"{s['lp_pct']:.1f}%")
    tab1,tab2=st.tabs(['Local Pack Coverage','Organic Heatmap'])
    with tab1:
        df=pd.DataFrame(st.session_state['data'])
        fig=create_scattermap(df,center['lat'],center['lng'],'lp_rank','Local Pack Coverage')
        st.plotly_chart(fig,use_container_width=True)
    with tab2:
        folmap=folium.Map(location=[center['lat'],center['lng']],zoom_start=12,tiles='CartoDB positron')
        HeatMap([(d['lat'],d['lng'],(11-d['org_rank'] if d['org_rank'] and d['org_rank']<=10 else 0)) for d in st.session_state['data']]).add_to(folmap)
        st_folium(folmap,key='organic_heatmap')
    df_full=pd.DataFrame(st.session_state['data'])
    st.download_button('Download CSV',df_full.to_csv(index=False),'results.csv',key='csv')
    st.download_button('Download JSON',json.dumps(st.session_state['data']),'results.json',key='json')

st.markdown('---')
st.write('© Built with Serpstack & Google Maps')
