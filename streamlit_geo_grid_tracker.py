"""
Streamlit SEO Geo-Grid Visibility Tracker

A comprehensive tool to track:
- Google local pack rankings
- Organic SERP rankings
- Google Maps listing rankings
across a customizable geographic grid, with competitor insights, historical comparisons, and exportable reports.

Requires:
- Google Maps API Key
- ScraperAPI Access Key (for Google SERP)

Run:
    streamlit run streamlit_geo_grid_tracker.py
"""

import os
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
from branca.colormap import LinearColormap
from streamlit_folium import st_folium

# --- Helper: build color map ---
def _build_colormap():
    return LinearColormap(['green','yellow','red'], vmin=1, vmax=10)

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

    def get_place_details(self, place_id: str):
        res = self.gmaps.place(place_id=place_id, fields=['name','rating','user_ratings_total','url'])
        return res.get('result', {})

    def find_place_id(self, name: str, loc: dict):
        resp = self.gmaps.find_place(
            input=name, input_type='textquery', fields=['place_id'],
            location_bias=f"point:{loc['lat']},{loc['lng']}"
        )
        cand = resp.get('candidates', [])
        return cand[0]['place_id'] if cand else None

    def gen_grid(self, lat0, lng0, radius, step, shape):
        pts=[]; lat_deg=radius/111; lng_deg=radius/(111*math.cos(math.radians(lat0)))
        rows=int(2*lat_deg/(step/111))+1; cols=int(2*lng_deg/(step/111))+1
        for i in range(rows):
            for j in range(cols):
                lat=lat0-lat_deg+(i*(2*lat_deg/(rows-1)) if rows>1 else 0)
                lng=lng0-lng_deg+(j*(2*lng_deg/(cols-1)) if cols>1 else 0)
                d=geodesic((lat0,lng0),(lat,lng)).km
                if shape=='Circle' and d>radius: continue
                pts.append({'lat':lat,'lng':lng,'dist_km':d})
        return pts

    def search_serp(self, kw, loc, lang='en', country='us'):
        url='https://api.scraperapi.com/structured/google/search'
        p={'api_key':self.scraper_key,'query':kw,'language':lang,'country':country}
        r=requests.get(url,params=p); return r.json() if r.ok else {}

    def search_places(self, kw, loc, radius_m=1000):
        res=self.gmaps.places_nearby(location=(loc['lat'],loc['lng']),radius=radius_m,keyword=kw)
        return res.get('results', [])

    def run(self, business, address, radius, step, keywords, shape, progress=None):
        center=self.geocode(address)
        target_id=self.find_place_id(business, center)
        target_info=self.get_place_details(target_id) if target_id else {}
        grid=self.gen_grid(center['lat'],center['lng'],radius,step,shape)
        total=len(grid)*len(keywords)
        out=[]; count=0
        for pt in grid:
            for kw in keywords:
                count+=1; progress.progress(count/total) if progress else None
                serp=self.search_serp(kw,pt)
                places=self.search_places(kw,pt)
                # find ranks
                org=next((i for i,r in enumerate(serp.get('organic_results',[]),1)
                          if business.lower() in r.get('title','').lower()), None)
                lp=next((i for i,r in enumerate(serp.get('local_results',[]),1)
                         if business.lower() in r.get('title','').lower()), None)
                gmp=None
                comp_list=[]
                for i,p in enumerate(places,1):
                    comp={'position':i,'name':p.get('name'),
                          'rating':p.get('rating'),
                          'reviews':p.get('user_ratings_total')}
                    comp_list.append(comp)
                    if p.get('place_id')==target_id: gmp=i
                rec={'keyword':kw,'lat':pt['lat'],'lng':pt['lng'],'dist_km':pt['dist_km'],
                     'org_rank':org,'lp_rank':lp,'gmp_rank':gmp,
                     'target_rating':target_info.get('rating'),
                     'target_reviews':target_info.get('user_ratings_total'),
                     'competitors':comp_list,'timestamp':datetime.datetime.now()}
                out.append(rec); time.sleep(0.5)
        self.results=out; return out

    def summarize(self):
        df=pd.DataFrame(self.results)
        tot=len(df)
        summary={'total_checks':tot,
                 'org_pct':df['org_rank'].notnull().mean()*100,
                 'lp_pct':df['lp_rank'].notnull().mean()*100,
                 'gmp_pct':df['gmp_rank'].notnull().mean()*100}
        summary.update({f'avg_{c}':df[c].dropna().mean() for c in ['org_rank','lp_rank','gmp_rank']})
        return summary

    def map(self, data, center, mode):
        m=folium.Map(location=center,zoom_start=13,tiles='CartoDB positron')
        cmap=_build_colormap(); cmap.caption=f"{mode.title()} Rank (1=Best)"
        hm=[(d['lat'],d['lng'],(11-d[mode] if d.get(mode) and d[mode]<=10 else 0))
            for d in data if d.get(mode)]
        HeatMap(hm,radius=25,gradient={0:'red',0.5:'yellow',1:'green'}).add_to(m)
        mc=MarkerCluster().add_to(m)
        for d in data:
            r=d.get(mode); col='gray' if not r else cmap(min(max(11-r,1),10))
            tt=(f"Rank: {r or 'X'}<br>KW: {d['keyword']}<br>Dist: {d['dist_km']:.2f}km"
                f"<br>My Biz: {d['target_rating']}★ ({d['target_reviews']} revs)")
            for c in d['competitors'][:3]: tt+=f"<br>{c['position']}. {c['name']} {c['rating']}★"
            folium.CircleMarker([d['lat'],d['lng']],radius=8,color=col,fill=True,
                                fill_color=col,fill_opacity=0.8,tooltip=tt).add_to(mc)
        folium.LayerControl().add_to(m); m.add_child(cmap)
        return m

# --- Streamlit App ---
st.set_page_config("SEO Geo-Grid Tracker",layout="wide")
st.title("🌐 SEO Geo-Grid Visibility Tracker")
# Sidebar
gkey=st.sidebar.text_input("Google Maps API Key",type='password')
skey=st.sidebar.text_input("ScraperAPI Key",type='password')
biz=st.sidebar.text_input("Business Profile Name")
addr=st.sidebar.text_input("Business Address","1600 Amphitheatre Parkway, Mountain View, CA")
shape=st.sidebar.selectbox("Grid Shape",['Circle','Square'])
rad=st.sidebar.slider("Radius (km)",0.5,10.0,2.0,0.5)
step=st.sidebar.slider("Spacing (km)",0.1,2.0,0.5,0.1)
keywords=[k.strip() for k in st.sidebar.text_area("Keywords (one per line)","coffee shop near me
espresso bar
café").split("\n") if k]
# Run Scan
if st.sidebar.button("Run Scan"):
    tracker=GeoGridTracker(gkey,skey)
    data=tracker.run(biz,addr,rad,step,keywords,shape,st.progress(0))
    st.session_state['data']=data
    st.session_state['summary']=tracker.summarize()
# Show Summary
if 'summary' in st.session_state:
    s=st.session_state['summary']
    cols=st.columns(4)
    cols[0].metric("Total Checks",s['total_checks'])
    cols[1].metric("Organic %",f"{s['org_pct']:.1f}%")
    cols[2].metric("Local Pack %",f"{s['lp_pct']:.1f}%")
    cols[3].metric("Maps %",f"{s['gmp_pct']:.1f}%")
# Tabs for each rank type
tab1,tab2,tab3=st.tabs(["Organic","Local Pack","Maps"])
for tab,mode in zip([tab1,tab2,tab3],['org_rank','lp_rank','gmp_rank']):
    with tab:
        data=st.session_state.get('data',[])
        if data:
            center=tracker.geocode(addr).values()
            m=tracker.map(data,tracker.geocode(addr),mode)
            st_folium(m,width=800,height=600)
            # Export
            df=pd.DataFrame(data)
            st.download_button("Download CSV",df.to_csv(index=False),"results.csv")
            st.download_button("Download JSON",json.dumps(data,default=str),"results.json")
        else:
            st.info("Run a scan to display results.")

st.markdown("---")
st.write("© Built with ScraperAPI & Google Maps API")
