"""
Streamlit SEO Geo-Grid Visibility Tracker

A web interface for tracking local, map‑pack, and Google Maps rankings across a geographic grid.
Requires:
- Google Maps API key
- ScraperAPI Access Key (for Google SERP data)

Run with: streamlit run streamlit_geo_grid_tracker.py
"""

import time
import json
import math
import datetime
import requests
import pandas as pd
import streamlit as st
import googlemaps
from geopy.distance import geodesic
import folium
from folium.plugins import HeatMap, MarkerCluster
from folium.features import DivIcon
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import plotly.graph_objects as go


def _build_colormap():
    # Green (top) -> Yellow (mid) -> Red (low)
    return LinearColormap(["green", "yellow", "red"], vmin=1, vmax=10)

class GeoGridTracker:
    def __init__(self, google_maps_api_key: str, scraperapi_key: str):
        self.google_maps_api_key = google_maps_api_key
        self.scraperapi_key = scraperapi_key
        self.results_data = []
        self.gmaps_client = googlemaps.Client(key=self.google_maps_api_key)

    def geocode_address(self, address: str):
        geocode = self.gmaps_client.geocode(address)
        if geocode:
            return geocode[0]
        st.error("Unable to geocode address.")
        return None

    def get_target_place_id(self, name: str, loc: dict) -> str:
        resp = self.gmaps_client.find_place(
            input=name, input_type='textquery', fields=['place_id'],
            location_bias=f"point:{loc['lat']},{loc['lng']}"
        )
        cands = resp.get('candidates', [])
        return cands[0]['place_id'] if cands else None

    def generate_grid(self, lat0, lng0, radius_km, spacing_km, shape="Circle"):
        pts = []
        lat_deg = radius_km/111.0
        lng_deg = radius_km/(111.0*math.cos(math.radians(lat0)))
        rows = int(2*lat_deg/(spacing_km/111.0))+1
        cols = int(2*lng_deg/(spacing_km/111.0))+1
        for i in range(rows):
            for j in range(cols):
                lat = lat0 - lat_deg + i*(2*lat_deg/(rows-1) if rows>1 else 0)
                lng = lng0 - lng_deg + j*(2*lng_deg/(cols-1) if cols>1 else 0)
                d = geodesic((lat0,lng0),(lat,lng)).km
                if shape=="Circle" and d>radius_km: continue
                pts.append({"lat":lat, "lng":lng, "dist":d})
        return pts

    def search_serp(self, kw, loc, lang="en", country="us"):
        url = "https://api.scraperapi.com/structured/google/search"
        params = {"api_key":self.scraperapi_key, "query":kw,
                  "language":lang, "country":country}
        r = requests.get(url, params=params)
        return r.json() if r.ok else {}

    def search_places(self, kw, loc, radius_m=1000):
        return self.gmaps_client.places_nearby(
            location=(loc['lat'],loc['lng']), radius=radius_m, keyword=kw
        ).get('results', [])

    def find_ranks(self, serp, places, target_name, target_id):
        org=next((i for i,(r) in enumerate(serp.get('organic_results',[]),1)
                  if target_name.lower() in r.get('title','').lower()), None)
        lp=next((i for i,(r) in enumerate(serp.get('local_results',[]),1)
                  if target_name.lower() in r.get('title','').lower()), None)
        gmp=next((i for i,p in enumerate(places,1)
                  if p.get('place_id')==target_id), None)
        return org,lp,gmp

    def run_geo_grid(self, name, lat0, lng0, rad, step, kws, shape, prog=None):
        pts=self.generate_grid(lat0,lng0,rad,step,shape)
        total=len(pts)*len(kws)
        tid=self.get_target_place_id(name, {'lat':lat0,'lng':lng0})
        out=[]
        for idx,pt in enumerate(pts):
            for j,kw in enumerate(kws):
                if prog: prog.progress((idx*len(kws)+j+1)/total)
                serp=self.search_serp(kw, pt)
                places=self.search_places(kw, pt)
                org,lp,gmp=self.find_ranks(serp,places,name,tid)
                out.append({
                    'keyword':kw, 'lat':pt['lat'], 'lng':pt['lng'],
                    'dist_km':pt['dist'], 'org':org, 'lp':lp, 'gmp':gmp
                })
                time.sleep(0.5)
        self.results_data=out
        return out

    def create_map(self,data, lat0, lng0, mode):
        m=folium.Map(location=[lat0,lng0], zoom_start=13, tiles='CartoDB positron')
        cmap=_build_colormap()
        fc=folium.FeatureGroup('Heatmap', show=False)
        hm_data=[(d['lat'],d['lng'],(11-d[mode] if d[mode] and d[mode]<=10 else 0))
                 for d in data if d.get(mode)]
        HeatMap(hm_data, radius=25, gradient={0:'red',0.5:'yellow',1:'green'}).add_to(fc)
        m.add_child(fc)
        clu=MarkerCluster(name='Points').add_to(m)
        for d in data:
            r=d.get(mode)
            color='lightgray'
            icon_html='×'
            if r:
                rnorm=min(max(11-r,1),10)
                color=cmap(r) if mode!='gmp' else cmap(r)
                icon_html=str(r)
            folium.CircleMarker([d['lat'],d['lng']], radius=10,
                                color=color, fill=True, fill_color=color, fill_opacity=0.8,
                                tooltip=f"{mode.upper()}: {r or 'X'}<br>KW: {d['keyword']}<br>Dist: {d['dist_km']:.2f}km"
            ).add_to(m)
        folium.map.LayerControl(collapsed=False).add_to(m)
        cmap.caption=f"Ranking ({mode.upper()}:1=best)"
        m.add_child(cmap)
        return m

# Streamlit UI
st.set_page_config('SEO Geo-Grid Tracker',layout='wide')
st.title('🌐 Geo-Grid Visibility Tracker')

# Sidebar
gkey=st.sidebar.text_input('Google Maps API Key',type='password')
skey=st.sidebar.text_input('ScraperAPI Key',type='password')
name=st.sidebar.text_input('Business Name')
addr=st.sidebar.text_input('Address','1600 Amphitheatre Parkway, Mountain View, CA')
shape=st.sidebar.selectbox('Grid Shape',['Circle','Square'])
rad=st.sidebar.slider('Radius (km)',0.5,10.0,2.0,0.5)
step=st.sidebar.slider('Spacing (km)',0.1,2.0,0.5,0.1)
kws=[k.strip() for k in st.sidebar.text_area('Keywords', 'coffee shop near me').split('\n') if k]

if st.sidebar.button('Run'):
    gc=GeoGridTracker(gkey,skey)
    loc=gc.geocode_address(addr)['geometry']['location']
    bar=st.progress(0)
    res=gc.run_geo_grid(name, loc['lat'], loc['lng'], rad, step, kws, shape, bar)
    st.session_state['data']=res
    st.success('Done')

# Tabs
tab1,tab2,tab3=st.tabs(['Map Pack','Organic','Maps'])
for tab,mode in zip([tab1,tab2,tab3],['lp','org','gmp']):
    with tab:
        data=st.session_state.get('data',[])
        if data:
            loc0=gc.geocode_address(addr)['geometry']['location']
            m=gc.create_map(data,loc0['lat'],loc0['lng'],mode)
            st_folium(m,width=800,height=600)
        else:
            st.info('Run a search first')

st.markdown('---')
st.write('Built with ScraperAPI & Google Maps API')
