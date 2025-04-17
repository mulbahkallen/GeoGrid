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
import plotly.express as px
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
        return None

    def generate_grid(self, center_lat, center_lng, radius_km, spacing_km, shape="Circle"):
        """Generate a grid of points around a center location."""
        grid_points = []
        # Convert radius from km to degrees
        lat_degree_radius = radius_km / 111.0
        lng_degree_radius = radius_km / (111.0 * math.cos(math.radians(center_lat)))
        # Calculate steps
        lat_steps = int(2 * lat_degree_radius / (spacing_km / 111.0)) + 1
        lng_steps = int(2 * lng_degree_radius / (spacing_km / (111.0 * math.cos(math.radians(center_lat))))) + 1
        for i in range(lat_steps):
            for j in range(lng_steps):
                point_lat = (center_lat - lat_degree_radius +
                             i * (2 * lat_degree_radius / (lat_steps - 1)) if lat_steps > 1 else center_lat)
                point_lng = (center_lng - lng_degree_radius +
                             j * (2 * lng_degree_radius / (lng_steps - 1)) if lng_steps > 1 else center_lng)
                distance = geodesic((center_lat, center_lng), (point_lat, point_lng)).kilometers
                if shape == "Circle":
                    if distance <= radius_km:
                        grid_points.append({"lat": point_lat, "lng": point_lng, "distance_km": distance})
                else:  # Square includes all points
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
        if data.get("status") == "OK" and len(data.get("candidates", [])) > 0:
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
            for i, result in enumerate(serp_data["organic_results"]):
                if business_name.lower() in result.get("title", "").lower():
                    organic_rank = i + 1
                    break
        local_pack_rank = None
        if "local_results" in serp_data:
            for i, result in enumerate(serp_data["local_results"]):
                if business_name.lower() in result.get("title", "").lower():
                    local_pack_rank = i + 1
                    break
        return {
            "organic_rank": organic_rank,
            "local_pack_rank": local_pack_rank,
            "is_in_organic": organic_rank is not None,
            "is_in_local_pack": local_pack_rank is not None
        }

    def run_geo_grid_tracking(self, business_name, center_lat, center_lng,
                              radius_km, spacing_km, keywords, shape="Circle", progress_bar=None):
        """Run geo-grid tracking for a business across multiple keywords."""
        grid_points = self.generate_grid(center_lat, center_lng, radius_km, spacing_km, shape)
        results = []
        total = len(grid_points) * len(keywords)
        count = 0
        for point in grid_points:
            for keyword in keywords:
                if progress_bar:
                    count += 1
                    progress_bar.progress(count / total)
                serp_data = self.search_serp(keyword, point)
                if not serp_data:
                    continue
                rank_data = self.find_business_rank(serp_data, business_name)
                results.append({
                    "business_name": business_name,
                    "keyword": keyword,
                    "lat": point["lat"],
                    "lng": point["lng"],
                    "distance_km": point["distance_km"],
                    "timestamp": datetime.datetime.now().isoformat(),
                    **rank_data
                })
                time.sleep(1)
        self.results_data = results
        return results

    def create_folium_map(self, data, center_lat, center_lng, result_type="local_pack_rank"):
        """Create a Folium map with the results."""
        m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
        for point in data:
            if point[result_type] is not None:
                normalized_rank = 11 - point[result_type] if point[result_type] <= 10 else 0
                folium.CircleMarker(
                    location=[point["lat"], point["lng"]],
                    radius=8,
                    color='blue' if result_type == 'local_pack_rank' else 'green',
                    fill=True,
                    fill_color=self._get_color_by_rank(normalized_rank),
                    fill_opacity=0.7,
                    popup=(
                        f"Keyword: {point['keyword']}<br>"
                        f"Organic Rank: {point['organic_rank']}<br>"
                        f"Local Pack Rank: {point['local_pack_rank']}<br>"
                        f"Distance: {point['distance_km']:.2f} km"
                    )
                ).add_to(m)
        heat_data = []
        for point in data:
            if point[result_type] is not None:
                weight = 1.0 if point[result_type] > 10 else (11 - point[result_type]) / 10.0
                heat_data.append([point["lat"], point["lng"], weight])
        if heat_data:
            HeatMap(heat_data).add_to(m)
        return m

    def _get_color_by_rank(self, normalized_rank):
        """Get color based on rank (0-10 scale, higher is better)."""
        if normalized_rank >= 8:
            return 'green'
        elif normalized_rank >= 5:
            return 'yellow'
        elif normalized_rank > 0:
            return 'orange'
        else:
            return 'red'

    def generate_summary_data(self):
        """Generate summary data for visualization."""
        if not self.results_data:
            return None
        df = pd.DataFrame(self.results_data)
        total_queries = len(df)
        organic_presence = df['is_in_organic'].sum()
        local_pack_presence = df['is_in_local_pack'].sum()
        summary = {
            "business_name": df['business_name'].iloc[0],
            "total_queries": total_queries,
            "organic_presence": organic_presence,
            "organic_presence_pct": organic_presence/total_queries*100,
            "local_pack_presence": local_pack_presence,
            "local_pack_presence_pct": local_pack_presence/total_queries*100,
        }
        if organic_presence > 0:
            summary["avg_organic_rank"] = df[df['organic_rank'].notnull()]['organic_rank'].mean()
        if local_pack_presence > 0:
            summary["avg_local_rank"] = df[df['local_pack_rank'].notnull()]['local_pack_rank'].mean()
        keyword_stats = df.groupby('keyword').agg({
            'is_in_organic': 'sum',
            'is_in_local_pack': 'sum',
            'organic_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()])>0 else None,
            'local_pack_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()])>0 else None
        }).reset_index()
        keyword_stats_list = []
        for _, row in keyword_stats.iterrows():
            count = len(df[df['keyword']==row['keyword']])
            ks = {
                "keyword": row['keyword'],
                "organic_presence": row['is_in_organic'],
                "organic_presence_pct": row['is_in_organic']/count*100,
                "local_pack_presence": row['is_in_local_pack'],
                "local_pack_presence_pct": row['is_in_local_pack']/count*100
            }
            if row['organic_rank'] is not None:
                ks["avg_organic_rank"] = row['organic_rank']
            if row['local_pack_rank'] is not None:
                ks["avg_local_pack_rank"] = row['local_pack_rank']
            keyword_stats_list.append(ks)
        df['distance_bin'] = pd.cut(df['distance_km'], bins=[0,1,2,5,float('inf')], labels=['0-1km','1-2km','2-5km','5km+'])
        distance_stats = df.groupby('distance_bin').agg({
            'is_in_organic':'mean',
            'is_in_local_pack':'mean',
            'organic_rank':lambda x: x[~x.isna()].mean() if len(x[~x.isna()])>0 else None,
            'local_pack_rank':lambda x: x[~x.isna()].mean() if len(x[~x.isna()])>0 else None
        }).reset_index()
        distance_stats_list = []
        for _, row in distance_stats.iterrows():
            ds = {
                "distance_bin": row['distance_bin'],
                "organic_presence_pct": row['is_in_organic']*100,
                "local_pack_presence_pct": row['is_in_local_pack']*100
            }
            if row['organic_rank'] is not None:
                ds["avg_organic_rank"] = row['organic_rank']
            if row['local_pack_rank'] is not None:
                ds["avg_local_pack_rank"] = row['local_pack_rank']
            distance_stats_list.append(ds)
        return {
            "summary": summary,
            "keyword_stats": keyword_stats_list,
            "distance_stats": distance_stats_list,
            "raw_data": df.to_dict('records')
        }

# Streamlit UI
st.set_page_config(page_title="SEO Geo-Grid Visibility Tracker", page_icon="� globe":), layout="wide")
st.title("🌐 SEO Geo-Grid Visibility Tracker")
st.markdown("Track local search visibility across a geographic grid. Enter a business profile name and address to center the grid.")

# Sidebar
st.sidebar.header("Configuration")
google_api_key = st.sidebar.text_input("Google Maps API Key", type="password")
serp_api_key = st.sidebar.text_input("SerpAPI Key", type="password")

st.sidebar.header("Business Details")
business_profile_name = st.sidebar.text_input("Business Profile Name", "")
business_address = st.sidebar.text_input("Business Address", "")

st.sidebar.header("Grid Options")
grid_shape = st.sidebar.selectbox("Grid Shape", ["Circle", "Square"])
radius_km = st.sidebar.slider("Radius (km)", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
spacing_km = st.sidebar.slider("Grid Spacing (km)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)

st.sidebar.header("Keywords")
keywords_input = st.sidebar.text_area("Keywords (one per line)", "coffee shop near me\nespresso bar\ncafé")
keywords = [k.strip() for k in keywords_input.split("\n") if k.strip()]

# Session state
if 'results' not in st.session_state:
    st.session_state.results = None
if 'summary_data' not in st.session_state:
    st.session_state.summary_data = None

# Grid preview
 def display_grid_preview():
    if not business_address or not google_api_key:
        st.warning("Please enter your business address and Google Maps API key")
        return
    tracker = GeoGridTracker(google_api_key, serp_api_key)
    place = tracker.geocode_address(business_address)
    if not place:
        st.error("Unable to geocode address")
        return
    loc = place["geometry"]["location"]
    pts = tracker.generate_grid(loc["lat"], loc["lng"], radius_km, spacing_km, grid_shape)
    m = folium.Map(location=[loc["lat"], loc["lng"]], zoom_start=12)
    folium.Marker(location=[loc["lat"], loc["lng"]], popup="Center", icon=folium.Icon(color='red')).add_to(m)
    if grid_shape == "Circle":
        folium.Circle(location=[loc["lat"], loc["lng"]], radius=radius_km*1000,
                      color='red', fill=True, fill_opacity=0.1).add_to(m)
    for p in pts:
        folium.CircleMarker(location=[p["lat"], p["lng"]], radius=3,
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
            loc = place["geometry"]["location"]
            my_bar = st.progress(0, text="Running geo-grid tracking...")
            results = tracker.run_geo_grid_tracking(
                business_profile_name, loc["lat"], loc["lng"],
                radius_km, spacing_km, keywords, shape=grid_shape, progress_bar=my_bar
            )
            st.session_state.results = results
            st.session_state.summary_data = tracker.generate_summary_data()
            st.session_state.center_lat = loc["lat"]
            st.session_state.center_lng = loc["lng"]

            my_bar.progress(1.0, text="Tracking complete!")
            st.success(f"✅ {len(results)} data points collected.")

with tab2:
    if st.session_state.results:
        st.header("Rankings Maps")
        result_type = st.radio("Result Type", ["local_pack_rank", "organic_rank"], horizontal=True,
                               format_func=lambda x: "Local Pack" if x=="local_pack_rank" else "Organic")
        for kw in keywords:
            st.subheader(f"Keyword: {kw}")
            map_data = [r for r in st.session_state.results if r["keyword"] == kw]
            tracker = GeoGridTracker(google_api_key, serp_api_key)
            m = tracker.create_folium_map(map_data, st.session_state.center_lat, st.session_state.center_lng, result_type)
            st_folium(m, height=500, width=800)
        st.markdown("""
        ### Map Legend
        - **Green markers**: Top rankings (1-3)
        - **Yellow markers**: Medium rankings (4-6)
        - **Orange markers**: Lower rankings (7-10)
        - **Red markers**: Not ranked or >10
        - **Heatmap**: Intensity = better visibility
        """)
    else:
        st.info("Run tracking to see results")

with tab3:
    if st.session_state.summary_data:
        summary = st.session_state.summary_data["summary"]
        keyword_stats = st.session_state.summary_data["keyword_stats"]
        distance_stats = st.session_state.summary_data["distance_stats"]

        st.header("SEO Visibility Summary")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Queries", summary["total_queries"])
        col2.metric("Organic Presence", f"{summary['organic_presence_pct']:.1f}%")
        col3.metric("Local Pack Presence", f"{summary['local_pack_presence_pct']:.1f}%")
        col4.metric("Avg. Local Pack Rank", f"{summary.get('avg_local_rank', 'N/A'):.1f}" if summary.get('avg_local_rank') else "N/A")

        st.subheader("Keyword Performance")
        kdf = pd.DataFrame(keyword_stats)
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(x=kdf['keyword'], y=kdf['organic_presence_pct'], name='Organic Presence', marker_color='blue'))
        fig1.add_trace(go.Bar(x=kdf['keyword'], y=kdf['local_pack_presence_pct'], name='Local Pack Presence', marker_color='green'))
        fig1.update_layout(title='Keyword Visibility (%)', xaxis_title='Keyword', yaxis_title='Presence %', barmode='group')
        st.plotly_chart(fig1, use_container_width=True)

        fig2 = go.Figure()
        if 'avg_organic_rank' in kdf:
            fig2.add_trace(go.Bar(x=kdf['keyword'], y=kdf['avg_organic_rank'], name='Avg. Organic Rank', marker_color='blue'))
        if 'avg_local_pack_rank' in kdf:
            fig2.add_trace(go.Bar(x=kdf['keyword'], y=kdf['avg_local_pack_rank'], name='Avg. Local Pack Rank', marker_color='green'))
        fig2.update_layout(title='Average Ranking by Keyword', xaxis_title='Keyword', yaxis_title='Avg Rank', barmode='group')
        st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Distance Analysis")
        ddf = pd.DataFrame(distance_stats)
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(x=ddf['distance_bin'], y=ddf['organic_presence_pct'], name='Organic Presence', marker_color='blue'))
        fig3.add_trace(go.Bar(x=ddf['distance_bin'], y=ddf['local_pack_presence_pct'], name='Local Pack Presence', marker_color='green'))
        fig3.update_layout(title='Visibility by Distance (%)', xaxis_title='Distance Bin', yaxis_title='Presence %', barmode='group')
        st.plotly_chart(fig3, use_container_width=True)

        fig4 = go.Figure()
        if 'avg_organic_rank' in ddf:
            fig4.add_trace(go.Bar(x=ddf['distance_bin'], y=ddf['avg_organic_rank'], name='Avg. Organic Rank', marker_color='blue'))
        if 'avg_local_pack_rank' in ddf:
            fig4.add_trace(go.Bar(x=ddf['distance_bin'], y=ddf['avg_local_pack_rank'], name='Avg. Local Pack Rank', marker_color='green'))
        fig4.update_layout(title='Average Ranking by Distance', xaxis_title='Distance', yaxis_title='Avg Rank', barmode='group')
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.info("Run tracking to see analytics")

with tab4:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        cols = ['business_name','keyword','lat','lng','distance_km','organic_rank','local_pack_rank','is_in_organic','is_in_local_pack','timestamp']
        display_df = df[cols]
        st.dataframe(display_df)
        st.download_button("Download CSV", data=df.to_csv(index=False), file_name="geo_grid_results.csv", mime="text/csv")
        st.download_button("Download JSON", data=json.dumps(st.session_state.results), file_name="geo_grid_results.json", mime="application/json")
    else:
        st.info("Run tracking to see raw data")

st.markdown("---")
st.markdown("SEO Geo-Grid Visibility Tracker | Build local SEO strategies with location-based ranking data")
