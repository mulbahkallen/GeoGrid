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
        
    def generate_grid(self, center_lat, center_lng, radius_km, spacing_km):
        """Generate a grid of points around a center location."""
        grid_points = []
        
        # Calculate bounds for a square that contains the circle
        # Convert radius from km to degrees (approximate)
        # 1 degree latitude is approximately 111 km
        lat_degree_radius = radius_km / 111.0
        # 1 degree longitude varies by latitude
        lng_degree_radius = radius_km / (111.0 * math.cos(math.radians(center_lat)))
        
        # Calculate number of steps in each direction
        lat_steps = int(2 * lat_degree_radius / (spacing_km / 111.0)) + 1
        lng_steps = int(2 * lng_degree_radius / (spacing_km / (111.0 * math.cos(math.radians(center_lat))))) + 1
        
        # Generate grid
        for i in range(lat_steps):
            for j in range(lng_steps):
                # Calculate lat/lng for this grid point
                point_lat = center_lat - lat_degree_radius + i * (2 * lat_degree_radius / (lat_steps - 1))
                point_lng = center_lng - lng_degree_radius + j * (2 * lng_degree_radius / (lng_steps - 1))
                
                # Check if point is within radius
                point_distance = geodesic((center_lat, center_lng), (point_lat, point_lng)).kilometers
                if point_distance <= radius_km:
                    grid_points.append({
                        "lat": point_lat,
                        "lng": point_lng,
                        "distance_km": point_distance
                    })
        
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
        
        if data["status"] == "OK" and len(data["candidates"]) > 0:
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
            data = response.json()
            return data
        except Exception as e:
            st.error(f"Error searching SERP: {e}")
            return None
    
    def find_business_rank(self, serp_data, business_name):
        """Find the rank of a business in SERP results."""
        # Check organic results
        organic_rank = None
        if "organic_results" in serp_data:
            for i, result in enumerate(serp_data["organic_results"]):
                if business_name.lower() in result.get("title", "").lower():
                    organic_rank = i + 1
                    break
        
        # Check local pack results
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
                             radius_km, spacing_km, keywords, progress_bar=None):
        """Run geo-grid tracking for a business across multiple keywords."""
        # Generate grid
        grid_points = self.generate_grid(center_lat, center_lng, radius_km, spacing_km)
        
        # Process each grid point
        results = []
        
        total_points = len(grid_points) * len(keywords)
        current_point = 0
        
        for point in grid_points:
            # Get place details to confirm business exists
            place = self.get_place_details(business_name, point)
            
            # For each keyword
            for keyword in keywords:
                # Update progress
                if progress_bar:
                    current_point += 1
                    progress_bar.progress(current_point / total_points)
                
                # Search SERP
                serp_data = self.search_serp(keyword, point)
                if not serp_data:
                    continue
                
                # Find business rank
                rank_data = self.find_business_rank(serp_data, business_name)
                
                # Store result
                result = {
                    "business_name": business_name,
                    "keyword": keyword,
                    "lat": point["lat"],
                    "lng": point["lng"],
                    "distance_km": point["distance_km"],
                    "timestamp": datetime.datetime.now().isoformat(),
                    **rank_data
                }
                
                results.append(result)
                
                # Be nice to the APIs - don't hammer them
                time.sleep(1)
        
        self.results_data = results
        return results
    
    def create_folium_map(self, data, center_lat, center_lng, result_type="local_pack_rank"):
        """Create a Folium map with the results."""
        # Create map
        m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
        
        # Add markers for each point with rank info
        for point in data:
            if point[result_type] is not None:
                # Invert rank for better visualization (higher rank = better)
                normalized_rank = 11 - point[result_type] if point[result_type] <= 10 else 0
                folium.CircleMarker(
                    location=[point["lat"], point["lng"]],
                    radius=8,
                    color='blue' if result_type == 'local_pack_rank' else 'green',
                    fill=True,
                    fill_color=self._get_color_by_rank(normalized_rank),
                    fill_opacity=0.7,
                    popup=f"Keyword: {point['keyword']}<br>"
                          f"Organic Rank: {point['organic_rank']}<br>"
                          f"Local Pack Rank: {point['local_pack_rank']}<br>"
                          f"Distance: {point['distance_km']:.2f} km"
                ).add_to(m)
        
        # Create heatmap of rankings
        heat_data = []
        for point in data:
            if point[result_type] is not None:
                # Higher weight for better rankings (lower numbers)
                weight = 1.0 if point[result_type] > 10 else (11 - point[result_type]) / 10.0
                heat_data.append([point["lat"], point["lng"], weight])
        
        if heat_data:
            HeatMap(heat_data).add_to(m)
            
        return m
    
    def _get_color_by_rank(self, normalized_rank):
        """Get color based on rank (0-10 scale, higher is better)."""
        if normalized_rank >= 8:  # Ranks 1-3
            return 'green'
        elif normalized_rank >= 5:  # Ranks 4-6
            return 'yellow'
        elif normalized_rank > 0:  # Ranks 7-10
            return 'orange'
        else:  # Not ranked or >10
            return 'red'
    
    def generate_summary_data(self):
        """Generate summary data for visualization."""
        if not self.results_data:
            return None
        
        # Convert to DataFrame for easier analysis
        df = pd.DataFrame(self.results_data)
        
        # Overall metrics
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
        
        # Average ranking when present
        if organic_presence > 0:
            summary["avg_organic_rank"] = df[df['organic_rank'].notnull()]['organic_rank'].mean()
        
        if local_pack_presence > 0:
            summary["avg_local_rank"] = df[df['local_pack_rank'].notnull()]['local_pack_rank'].mean()
        
        # Keyword stats
        keyword_stats = df.groupby('keyword').agg({
            'is_in_organic': 'sum',
            'is_in_local_pack': 'sum',
            'organic_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()]) > 0 else None,
            'local_pack_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()]) > 0 else None
        }).reset_index()
        
        keyword_stats_list = []
        for _, row in keyword_stats.iterrows():
            keyword = row['keyword']
            keyword_count = len(df[df['keyword'] == keyword])
            
            keyword_stat = {
                "keyword": keyword,
                "organic_presence": row['is_in_organic'],
                "organic_presence_pct": row['is_in_organic']/keyword_count*100,
                "local_pack_presence": row['is_in_local_pack'],
                "local_pack_presence_pct": row['is_in_local_pack']/keyword_count*100
            }
            
            if not pd.isna(row['organic_rank']):
                keyword_stat["avg_organic_rank"] = row['organic_rank']
            
            if not pd.isna(row['local_pack_rank']):
                keyword_stat["avg_local_pack_rank"] = row['local_pack_rank']
                
            keyword_stats_list.append(keyword_stat)
        
        # Distance analysis
        df['distance_bin'] = pd.cut(df['distance_km'], 
                                    bins=[0, 1, 2, 5, float('inf')], 
                                    labels=['0-1km', '1-2km', '2-5km', '5km+'])
        
        distance_stats = df.groupby('distance_bin').agg({
            'is_in_organic': 'mean',
            'is_in_local_pack': 'mean',
            'organic_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()]) > 0 else None,
            'local_pack_rank': lambda x: x[~x.isna()].mean() if len(x[~x.isna()]) > 0 else None
        }).reset_index()
        
        distance_stats_list = []
        for _, row in distance_stats.iterrows():
            distance_stat = {
                "distance_bin": row['distance_bin'],
                "organic_presence_pct": row['is_in_organic']*100,
                "local_pack_presence_pct": row['is_in_local_pack']*100
            }
            
            if not pd.isna(row['organic_rank']):
                distance_stat["avg_organic_rank"] = row['organic_rank']
            
            if not pd.isna(row['local_pack_rank']):
                distance_stat["avg_local_pack_rank"] = row['local_pack_rank']
                
            distance_stats_list.append(distance_stat)
        
        return {
            "summary": summary,
            "keyword_stats": keyword_stats_list,
            "distance_stats": distance_stats_list,
            "raw_data": df.to_dict('records')
        }


# Set page config
st.set_page_config(
    page_title="SEO Geo-Grid Visibility Tracker",
    page_icon="🌐",
    layout="wide"
)

# App title and description
st.title("🌐 SEO Geo-Grid Visibility Tracker")
st.markdown("""
This app helps you track how your business ranks in Google search results across a geographic area.
You can see how rankings change based on location, compare organic vs. local pack results, and identify areas for improvement.
""")

# Sidebar for configuration
st.sidebar.header("Configuration")

# API keys
google_api_key = st.sidebar.text_input("Google Maps API Key", type="password")
serp_api_key = st.sidebar.text_input("SerpAPI Key", type="password")

# Business details
st.sidebar.header("Business Details")
business_name = st.sidebar.text_input("Business Name", "Starbucks")

# Location
st.sidebar.header("Location")
center_lat = st.sidebar.number_input("Center Latitude", value=37.7749, format="%.6f")
center_lng = st.sidebar.number_input("Center Longitude", value=-122.4194, format="%.6f")
radius_km = st.sidebar.slider("Radius (km)", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
spacing_km = st.sidebar.slider("Grid Spacing (km)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)

# Keywords
st.sidebar.header("Keywords")
keywords_input = st.sidebar.text_area("Keywords (one per line)", "coffee shop near me\nespresso bar\ncafé")
keywords = [k.strip() for k in keywords_input.split("\n") if k.strip()]

# Initialize session state for results
if 'results' not in st.session_state:
    st.session_state.results = None
if 'summary_data' not in st.session_state:
    st.session_state.summary_data = None

# Function to display grid preview
def display_grid_preview():
    if not center_lat or not center_lng or not radius_km or not spacing_km:
        st.warning("Please set location parameters")
        return
    
    tracker = GeoGridTracker("dummy_key", "dummy_key")
    grid_points = tracker.generate_grid(center_lat, center_lng, radius_km, spacing_km)
    
    # Create map
    m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
    
    # Add center marker
    folium.Marker(
        location=[center_lat, center_lng],
        popup="Center Location",
        icon=folium.Icon(color='red', icon='info-sign')
    ).add_to(m)
    
    # Add radius circle
    folium.Circle(
        location=[center_lat, center_lng],
        radius=radius_km * 1000,  # Convert to meters
        color='red',
        fill=True,
        fill_opacity=0.1
    ).add_to(m)
    
    # Add grid points
    for point in grid_points:
        folium.CircleMarker(
            location=[point["lat"], point["lng"]],
            radius=2,
            color='blue',
            fill=True,
            fill_opacity=0.5
        ).add_to(m)
    
    # Display map
    st_folium(m, height=400, width=700)
    
    st.info(f"Generated {len(grid_points)} grid points within {radius_km}km radius")


# Main area tabs
tab1, tab2, tab3, tab4 = st.tabs(["Setup & Run", "Results Map", "Analytics", "Raw Data"])

# Tab 1: Setup & Run
with tab1:
    st.header("Grid Preview")
    st.markdown("This preview shows the points that will be used for tracking. Each point represents a location where a search will be simulated.")
    display_grid_preview()
    
    st.header("Run Tracking")
    if st.button("Start Tracking", disabled=not (google_api_key and serp_api_key and business_name and keywords)):
        if not google_api_key or not serp_api_key:
            st.error("Please enter your API keys in the sidebar")
        elif not business_name:
            st.error("Please enter a business name")
        elif not keywords:
            st.error("Please enter at least one keyword")
        else:
            # Create tracker
            tracker = GeoGridTracker(google_api_key, serp_api_key)
            
            # Show progress
            progress_text = "Running geo-grid tracking. This may take several minutes..."
            my_bar = st.progress(0, text=progress_text)
            
            # Run tracking
            results = tracker.run_geo_grid_tracking(
                business_name,
                center_lat,
                center_lng,
                radius_km,
                spacing_km,
                keywords,
                progress_bar=my_bar
            )
            
            # Store results
            st.session_state.results = results
            
            # Generate summary data
            st.session_state.summary_data = tracker.generate_summary_data()
            
            # Show success
            my_bar.progress(1.0, text="Tracking complete!")
            st.success(f"✅ Tracking complete! {len(results)} data points collected.")
            
            # Switch to results tab
            st.balloons()

# Tab 2: Results Map
with tab2:
    if st.session_state.results:
        st.header("Rankings Map")
        
        # Convert results to DataFrame
        df = pd.DataFrame(st.session_state.results)
        
        # Filter options
        col1, col2 = st.columns(2)
        with col1:
            selected_keyword = st.selectbox("Select Keyword", ["All"] + list(df["keyword"].unique()))
        with col2:
            result_type = st.radio("Result Type", ["local_pack_rank", "organic_rank"], horizontal=True,
                                 format_func=lambda x: "Local Pack" if x == "local_pack_rank" else "Organic")
        
        # Filter data
        if selected_keyword != "All":
            map_data = df[df["keyword"] == selected_keyword].to_dict('records')
        else:
            map_data = st.session_state.results
        
        # Create map
        tracker = GeoGridTracker("dummy_key", "dummy_key")
        m = tracker.create_folium_map(map_data, center_lat, center_lng, result_type)
        
        # Display map
        st_folium(m, height=600, width=800)
        
        # Map legend
        st.markdown("""
        ### Map Legend
        - **Green markers**: Top rankings (positions 1-3)
        - **Yellow markers**: Medium rankings (positions 4-6)
        - **Orange markers**: Lower rankings (positions 7-10)
        - **Red markers**: Not ranked or rank >10
        - **Heatmap**: Areas with better rankings show as more intense
        """)
    else:
        st.info("Run tracking to see results on the map")

# Tab 3: Analytics
with tab3:
    if st.session_state.summary_data:
        summary = st.session_state.summary_data["summary"]
        keyword_stats = st.session_state.summary_data["keyword_stats"]
        distance_stats = st.session_state.summary_data["distance_stats"]
        
        st.header("SEO Visibility Summary")
        
        # Overall metrics in cards
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Queries", summary["total_queries"])
        with col2:
            st.metric("Organic Presence", f"{summary['organic_presence_pct']:.1f}%")
        with col3:
            st.metric("Local Pack Presence", f"{summary['local_pack_presence_pct']:.1f}%")
        with col4:
            if "avg_local_rank" in summary:
                st.metric("Avg. Local Pack Rank", f"{summary['avg_local_rank']:.1f}")
            else:
                st.metric("Avg. Local Pack Rank", "N/A")
        
        # Keyword breakdown
        st.subheader("Keyword Performance")
        
        # Create dataframe for display
        keyword_df = pd.DataFrame(keyword_stats)
        
        # Create bar chart for keyword presence
        keyword_fig = go.Figure()
        
        # Add bars for organic and local pack presence
        if "organic_presence_pct" in keyword_df.columns:
            keyword_fig.add_trace(go.Bar(
                x=keyword_df["keyword"],
                y=keyword_df["organic_presence_pct"],
                name="Organic Presence",
                marker_color='blue'
            ))
        
        if "local_pack_presence_pct" in keyword_df.columns:
            keyword_fig.add_trace(go.Bar(
                x=keyword_df["keyword"],
                y=keyword_df["local_pack_presence_pct"],
                name="Local Pack Presence",
                marker_color='green'
            ))
        
        keyword_fig.update_layout(
            title="Keyword Visibility (%)",
            xaxis_title="Keyword",
            yaxis_title="Presence %",
            barmode='group'
        )
        
        st.plotly_chart(keyword_fig, use_container_width=True)
        
        # Average rank by keyword
        rank_fig = go.Figure()
        
        # Add lines for organic and local pack rank
        if "avg_organic_rank" in keyword_df.columns:
            rank_fig.add_trace(go.Bar(
                x=keyword_df["keyword"],
                y=keyword_df["avg_organic_rank"],
                name="Avg. Organic Rank",
                marker_color='blue'
            ))
        
        if "avg_local_pack_rank" in keyword_df.columns:
            rank_fig.add_trace(go.Bar(
                x=keyword_df["keyword"],
                y=keyword_df["avg_local_pack_rank"],
                name="Avg. Local Pack Rank",
                marker_color='green'
            ))
        
        rank_fig.update_layout(
            title="Average Ranking by Keyword (lower is better)",
            xaxis_title="Keyword",
            yaxis_title="Average Rank",
            barmode='group'
        )
        
        st.plotly_chart(rank_fig, use_container_width=True)
        
        # Distance analysis
        st.subheader("Distance Analysis")
        
        # Create dataframe for display
        distance_df = pd.DataFrame(distance_stats)
        
        # Create bar chart for distance presence
        distance_fig = go.Figure()
        
        # Add bars for organic and local pack presence by distance
        if "organic_presence_pct" in distance_df.columns:
            distance_fig.add_trace(go.Bar(
                x=distance_df["distance_bin"],
                y=distance_df["organic_presence_pct"],
                name="Organic Presence",
                marker_color='blue'
            ))
        
        if "local_pack_presence_pct" in distance_df.columns:
            distance_fig.add_trace(go.Bar(
                x=distance_df["distance_bin"],
                y=distance_df["local_pack_presence_pct"],
                name="Local Pack Presence",
                marker_color='green'
            ))
        
        distance_fig.update_layout(
            title="Visibility by Distance (%)",
            xaxis_title="Distance",
            yaxis_title="Presence %",
            barmode='group'
        )
        
        st.plotly_chart(distance_fig, use_container_width=True)
        
        # Average rank by distance
        dist_rank_fig = go.Figure()
        
        # Add lines for organic and local pack rank by distance
        if "avg_organic_rank" in distance_df.columns:
            dist_rank_fig.add_trace(go.Bar(
                x=distance_df["distance_bin"],
                y=distance_df["avg_organic_rank"],
                name="Avg. Organic Rank",
                marker_color='blue'
            ))
        
        if "avg_local_pack_rank" in distance_df.columns:
            dist_rank_fig.add_trace(go.Bar(
                x=distance_df["distance_bin"],
                y=distance_df["avg_local_pack_rank"],
                name="Avg. Local Pack Rank",
                marker_color='green'
            ))
        
        dist_rank_fig.update_layout(
            title="Average Ranking by Distance (lower is better)",
            xaxis_title="Distance",
            yaxis_title="Average Rank",
            barmode='group'
        )
        
        st.plotly_chart(dist_rank_fig, use_container_width=True)
    else:
        st.info("Run tracking to see analytics")

# Tab 4: Raw Data
with tab4:
    if st.session_state.results:
        st.header("Raw Data")
        df = pd.DataFrame(st.session_state.results)
        
        # Create dataframe with clean column names
        display_df = df.copy()
        
        # Reorder columns for better display
        col_order = ['business_name', 'keyword', 'lat', 'lng', 'distance_km', 
                     'organic_rank', 'local_pack_rank', 'is_in_organic', 'is_in_local_pack', 
                     'timestamp']
        display_df = display_df[col_order]
        
        # Display table
        st.dataframe(display_df)
        
        # Download options
        col1, col2 = st.columns(2)
        with col1:
            csv = df.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name="geo_grid_results.csv",
                mime="text/csv",
            )
        with col2:
            json_str = json.dumps(st.session_state.results)
            st.download_button(
                label="Download JSON",
                data=json_str,
                file_name="geo_grid_results.json",
                mime="application/json",
            )
    else:
        st.info("Run tracking to see raw data")

# Footer
st.markdown("---")
st.markdown("SEO Geo-Grid Visibility Tracker | Build local SEO strategies with location-based ranking data")
