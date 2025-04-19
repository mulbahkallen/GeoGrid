import time
import datetime
import json
import math
import re
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
from bs4 import BeautifulSoup

# --- HTTP retry session ---
session = requests.Session()
from requests.adapters import HTTPAdapter, Retry
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# --- Serpstack & ScraperAPI helpers ---
def serpstack_location_api(api_key, city_query):
    url = 'https://api.serpstack.com/locations'
    params = {'access_key': api_key, 'query': city_query, 'limit': 1}
    try:
        r = session.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0].get('canonical_name')
    except Exception as e:
        st.error(f"Location API error: {str(e)}")
    return None

def scraper_api_search(api_key, query, location, domain):
    """Use ScraperAPI with specific parameters to get SERP data"""
    url = 'https://api.scraperapi.com/scrape'
    
    # Format the search query to include location
    search_url = f"https://www.google.com/search?q={requests.utils.quote(query)}&num=100"
    
    # Set up specific location parameters
    payload = {
        'api_key': api_key,
        'url': search_url,
        'render': True,
        'country_code': 'us',
        'device_type': 'desktop',
        'auto_extract': True,  # Enable auto extraction of structured data
        'premium_proxy': True  # Use premium proxies for better success rate
    }
    
    # Add location parameter if provided
    if location:
        payload['location'] = location
    
    try:
        response = session.post(url, json=payload, timeout=60)
        response.raise_for_status()
        return response.text
    except Exception as e:
        st.error(f"ScraperAPI error: {str(e)}")
        return None

def parse_serp_results(html_content, business_name, domain):
    """Enhanced parser for organic and local pack results"""
    if not html_content:
        return None, None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    results = {
        'local_pack': None,
        'organic': None
    }
    
    # Convert to lowercase for case-insensitive matching
    business_name_lower = business_name.lower()
    domain_lower = domain.lower()
    
    # 1. Find local pack results (multiple possible class names)
    local_pack_selectors = [
        'div.VkpGBb', 'div.H93uF', 'div[data-local-attribute="local"]', 
        'div.local-pack', 'div.gws-local-packed__full', 'div.D5gswe'
    ]
    
    local_pack = None
    for selector in local_pack_selectors:
        local_pack = soup.select_one(selector)
        if local_pack:
            break
    
    if local_pack:
        # Look for business listings with various possible selectors
        possible_listings = []
        possible_listings.extend(local_pack.find_all('div', {'class': 'rllt__details'}))
        possible_listings.extend(local_pack.find_all('div', {'class': 'dbg0pd'}))
        possible_listings.extend(local_pack.find_all('div', {'class': 'cXedhc'}))
        
        for idx, listing in enumerate(possible_listings, 1):
            text_content = listing.get_text().lower()
            if business_name_lower in text_content:
                results['local_pack'] = idx
                break
    
    # 2. Find organic results with improved selectors
    organic_results = []
    organic_results.extend(soup.find_all('div', {'class': 'g'}))
    organic_results.extend(soup.find_all('div', {'class': 'tF2Cxc'}))
    organic_results.extend(soup.find_all('div', {'class': 'yuRUbf'}))
    
    # Deduplicate results
    seen_urls = set()
    unique_organic_results = []
    for result in organic_results:
        link = result.find('a')
        if link and link.get('href'):
            url = link.get('href')
            if url not in seen_urls:
                seen_urls.add(url)
                unique_organic_results.append(result)
    
    # Find the business in organic results
    for idx, result in enumerate(unique_organic_results, 1):
        link = result.find('a')
        if link and link.get('href'):
            url = link.get('href').lower()
            title = result.find('h3')
            title_text = title.get_text().lower() if title else ""
            
            # Check for domain match or business name in title
            if domain_lower in url or business_name_lower in title_text:
                results['organic'] = idx
                break
    
    return results['local_pack'], results['organic']

def serpstack_search(api_key, query, location_name):
    """Original Serpstack search function"""
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
    except Exception as e:
        st.error(f"SerpStack API error: {str(e)}")
    return {}

# --- Google Places helpers ---
def google_places_fetch(lat, lon, keyword, api_key):
    base = 'https://maps.googleapis.com/maps/api/place/nearbysearch/json'
    params = {'location': f"{lat},{lon}", 'keyword': keyword, 'rankby': 'distance', 'key': api_key}
    all_results = []
    
    try:
        for _ in range(3):
            r = session.get(base, params=params, timeout=30)
            if not r.ok: 
                st.error(f"Google Places API error: {r.status_code}")
                break
            data = r.json()
            all_results.extend(data.get('results', []))
            token = data.get('next_page_token')
            if not token: break
            time.sleep(2)
            params['pagetoken'] = token
            
        structured = []
        for p in all_results:
            structured.append({
                'place_id': p.get('place_id'),
                'name': p.get('name','').lower(),
                'rating': float(p.get('rating') or 0),
                'reviews': int(p.get('user_ratings_total') or 0),
                'vicinity': p.get('vicinity', '')
            })
        structured.sort(key=lambda x: (-x['rating'], -x['reviews'], x['name']))
        return structured
    except Exception as e:
        st.error(f"Google Places fetch error: {str(e)}")
        return []

def google_places_rank(lat, lon, business_name, domain, api_key):
    spots = google_places_fetch(lat, lon, business_name, api_key)
    map_rank = None
    business_name_lower = business_name.lower()
    
    for idx, item in enumerate(spots, start=1):
        if business_name_lower in item['name']:
            map_rank = idx
            break
    
    return map_rank, spots[:10]  # Return top 10 competitors for analysis

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
            color, text = 'gray','X'
        else:
            r = int(r)
            if r <= 3: color='green'
            elif r <= 10: color='orange'
            else: color='red'
            text = str(r)
        fig.add_trace(go.Scattermapbox(
            lat=[row['lat']], lon=[row['lng']], mode='markers+text',
            marker=dict(size=20, color=color), text=[text], textposition='middle center',
            textfont=dict(size=14, color='white'), hoverinfo='text',
            hovertext=f"Keyword: {row['keyword']}<br>Rank: {text}<br>Dist: {row['dist_km']:.2f}km",
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

def create_organic_heatmap(data, center_lat, center_lng):
    """Create a folium heatmap with improved weighting"""
    folmap = folium.Map(location=[center_lat, center_lng], zoom_start=12, 
                        tiles='CartoDB positron')
    
    # Process heat data with better weighting
    heat_data = []
    for d in data:
        rank = d.get('org_rank')
        # Use inverse of rank as weight (higher for better positions)
        if rank and rank <= 10:
            # Weight: 10 for position 1, 9 for position 2, etc.
            weight = 11 - rank
            heat_data.append([d['lat'], d['lng'], weight])
        elif rank and rank <= 20:
            # Lower weight for positions 11-20
            weight = (21 - rank) / 2
            heat_data.append([d['lat'], d['lng'], weight])
    
    # Only add heatmap if we have data
    if heat_data:
        HeatMap(heat_data, 
                min_opacity=0.4,
                gradient={0.4: 'blue', 0.65: 'yellow', 0.9: 'red'},
                radius=20).add_to(folmap)
    
    # Add marker for business location
    folium.Marker([center_lat, center_lng], 
                  popup="Business Location",
                  icon=folium.Icon(color='green', icon='info-sign')).add_to(folmap)
    
    return folmap

# --- Competitor Analysis ---
def analyze_competitors(all_competitors):
    """Analyze top competitors from Google Places data"""
    if not all_competitors:
        return pd.DataFrame()
    
    # Flatten the list of lists
    all_spots = []
    for competitors in all_competitors:
        if competitors:
            all_spots.extend(competitors)
    
    if not all_spots:
        return pd.DataFrame()
    
    # Count occurrences of each business
    business_counts = {}
    for spot in all_spots:
        name = spot['name']
        if name in business_counts:
            business_counts[name]['count'] += 1
            business_counts[name]['avg_rating'] = (business_counts[name]['avg_rating'] * 
                                                  (business_counts[name]['count']-1) + 
                                                  spot['rating']) / business_counts[name]['count']
            business_counts[name]['total_reviews'] += spot['reviews']
        else:
            business_counts[name] = {
                'count': 1,
                'avg_rating': spot['rating'],
                'total_reviews': spot['reviews'],
                'vicinity': spot['vicinity']
            }
    
    # Convert to DataFrame
    df_competitors = pd.DataFrame([
        {
            'business_name': name,
            'appearance_count': data['count'],
            'avg_rating': round(data['avg_rating'], 1),
            'total_reviews': data['total_reviews'],
            'address': data['vicinity']
        }
        for name, data in business_counts.items()
    ])
    
    # Sort by appearance count
    df_competitors = df_competitors.sort_values('appearance_count', ascending=False).reset_index(drop=True)
    return df_competitors

# --- GeoGridTracker ---
class GeoGridTracker:
    def __init__(self, serp_key, gmaps_key, scraper_key=None):
        self.serpkey = serp_key
        self.gmaps_key = gmaps_key
        self.scraper_key = scraper_key
        self.gmaps = googlemaps.Client(key=gmaps_key)
        self.results = []
        self.competitors = []

    def geocode(self, addr):
        try:
            res = self.gmaps.geocode(addr)
            return res[0]['geometry']['location'] if res else None
        except Exception as e:
            st.error(f"Geocoding error: {str(e)}")
            return None

    def reverse_city(self, lat, lng):
        try:
            resp = self.gmaps.reverse_geocode((lat,lng), result_type=['locality','administrative_area_level_1'])
            for comp in resp[0]['address_components']:
                if 'locality' in comp['types'] or 'administrative_area_level_1' in comp['types']:
                    return comp['long_name']
            return None
        except Exception as e:
            st.error(f"Reverse geocoding error: {str(e)}")
            return None

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

    def run_scan(self, business, website, radius, step, shape, progress=None):
        center = self.geocode(business)
        if not center: return []
    
        # Normalize domain format
        domain = urlparse(website).netloc.lower()
        if not domain:  # If urlparse couldn't extract the domain
            domain = website.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            if not (domain.startswith('http://') or domain.startswith('https://')):
                domain = domain.split('/')[0]  # Get just the domain part
    
        grid = self.gen_grid(center['lat'], center['lng'], radius, step, shape)
        total = len(grid)
        out = []
        all_competitors = []
    
        for idx, pt in enumerate(grid, start=1):
            if progress: progress.progress(idx/total)
            city = self.reverse_city(pt['lat'], pt['lng']) or ''
            
            # Get location for search
            locn = serpstack_location_api(self.serpkey, city)
            location_str = locn or city
            
            # Attempt SERP results with ScraperAPI first
            lp, org = None, None
            if self.scraper_key:
                html_content = scraper_api_search(self.scraper_key, business, location_str, domain)
                lp, org = parse_serp_results(html_content, business, domain)
            
            # Fallback to SerpStack if needed
            if (lp is None or org is None) and self.serpkey:
                serp = serpstack_search(self.serpkey, business, location_str)
                if org is None:
                    org = next((i for i, r in enumerate(serp.get('organic_results', []),1)
                            if domain in r.get('url','').lower() or business.lower() in r.get('title','').lower()), None)
                if lp is None:
                    lp = next((i for i, r in enumerate(serp.get('local_results', []),1)
                           if business.lower() in r.get('title','').lower()), None)
            
            # Get Google Maps ranking
            gmp, top_competitors = google_places_rank(pt['lat'], pt['lng'], business, domain, self.gmaps_key)
            all_competitors.append(top_competitors)
            
            out.append({
                'keyword': business,
                'lat': pt['lat'],
                'lng': pt['lng'],
                'dist_km': pt['dist_km'],
                'org_rank': org,
                'lp_rank': lp,
                'gmp_rank': gmp,
                'location': location_str
            })
            
            # Throttle requests
            time.sleep(2)  # Increased wait time to avoid rate limits
        
        self.results = out
        self.competitors = all_competitors
        st.session_state['center'] = center
        st.session_state['competitors'] = analyze_competitors(all_competitors)
        return out

# --- Streamlit UI ---
st.set_page_config(page_title='SEO Geo-Grid Tracker', layout='wide')
st.title('🌐 SEO Geo-Grid Visibility Tracker')

# Sidebar
with st.sidebar:
    st.header("API Keys")
    gmaps_key = st.text_input('Google Maps API Key', type='password')
    serp_key = st.text_input('Serpstack API Key', type='password')
    scraper_key = st.text_input('ScraperAPI Key (optional)', type='password', 
                              help="Using ScraperAPI improves SERP result detection")
    
    st.header("Business Information")
    business = st.text_input('Business Profile Name')
    website = st.text_input('Website URL')
    
    st.header("Grid Configuration")
    shape = st.selectbox('Grid Shape', ['Circle','Square'])
    radius = st.slider('Radius (km)', 0.5, 10.0, 2.0, 0.5)
    step = st.slider('Spacing (km)', 0.1, 2.0, 0.5, 0.1)
    
    tracker = GeoGridTracker(serp_key, gmaps_key, scraper_key) if serp_key and gmaps_key else None
    
    if tracker and business and website:
        if st.button('Run Scan', type="primary"):
            with st.spinner('Running scan... This may take a few minutes'):
                prog = st.progress(0)
                data = tracker.run_scan(business, website, radius, step, shape, prog)
                st.session_state['data'] = data
                df = pd.DataFrame(data)
                
                # Calculate visibility metrics
                st.session_state['summary'] = {
                    'total': len(df),
                    'org_pct': df['org_rank'].notna().mean()*100,
                    'org_top3': (df['org_rank'] <= 3).mean()*100 if not df['org_rank'].empty else 0,
                    'lp_pct': df['lp_rank'].notna().mean()*100,
                    'lp_top3': (df['lp_rank'] <= 3).mean()*100 if not df['lp_rank'].empty else 0,
                    'gmp_pct': df['gmp_rank'].notna().mean()*100,
                    'gmp_top3': (df['gmp_rank'] <= 3).mean()*100 if not df['gmp_rank'].empty else 0
                }
                st.success('Scan completed!')
    else:
        st.warning('Enter all required fields to run scan')

# Display results
if 'data' in st.session_state:
    s = st.session_state['summary']
    
    # Summary KPIs
    st.header("Visibility Summary")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.subheader("Local Pack Coverage")
        st.metric("Visibility", f"{s['lp_pct']:.1f}%")
        st.metric("Top 3 Positions", f"{s['lp_top3']:.1f}%")
    
    with col2:
        st.subheader("Organic Coverage")
        st.metric("Visibility", f"{s['org_pct']:.1f}%")
        st.metric("Top 3 Positions", f"{s['org_top3']:.1f}%")
    
    with col3:
        st.subheader("Maps Coverage")
        st.metric("Visibility", f"{s['gmp_pct']:.1f}%")
        st.metric("Top 3 Positions", f"{s['gmp_top3']:.1f}%")
    
    # Main visualization tabs
    tab1, tab2, tab3, tab4 = st.tabs(['Local Pack Coverage', 'Organic Coverage', 'Maps Coverage', 'Competitor Analysis'])
    
    center = st.session_state['center']
    df = pd.DataFrame(st.session_state['data'])
    
    with tab1:
        fig1 = create_scattermap(df, center['lat'], center['lng'], 'lp_rank', 'Local Pack Coverage')
        st.plotly_chart(fig1, use_container_width=True)
        
        # Local Pack data table
        st.subheader("Local Pack Rankings Detail")
        lp_data = df[['location', 'dist_km', 'lp_rank']].copy()
        lp_data.columns = ['Location', 'Distance (km)', 'Local Pack Rank']
        st.dataframe(lp_data.sort_values('Distance (km)'))
    
    with tab2:
    # Organic heatmap
    st.subheader("Organic Rankings Heatmap")
    
    # Create heatmap with improved function
    folmap = create_organic_heatmap(st.session_state['data'], center['lat'], center['lng'])
    
    # Display the map
    st_folium(folmap, key='organic_heatmap', width=700, height=500)
    
    # Create metrics for organic visibility
    org_visible = df['org_rank'].notna().sum()
    org_top10 = (df['org_rank'] <= 10).sum() if not df['org_rank'].empty else 0
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Visible in Organic Results", f"{org_visible} / {len(df)} locations")
    with col2:
        st.metric("Top 10 Positions", f"{org_top10} / {len(df)} locations")
    
    # Organic data table
    st.subheader("Organic Rankings Detail")
    org_data = df[['location', 'dist_km', 'org_rank']].copy()
    org_data.columns = ['Location', 'Distance (km)', 'Organic Rank']
    st.dataframe(org_data.sort_values('Organic Rank').dropna(subset=['Organic Rank']))
    
    with tab3:
        fig3 = create_scattermap(df, center['lat'], center['lng'], 'gmp_rank', 'Google Maps Coverage')
        st.plotly_chart(fig3, use_container_width=True)
        
        # Maps data table
        st.subheader("Google Maps Rankings Detail")
        maps_data = df[['location', 'dist_km', 'gmp_rank']].copy() 
        maps_data.columns = ['Location', 'Distance (km)', 'Maps Rank']
        st.dataframe(maps_data.sort_values('Distance (km)'))
    
    with tab4:
        st.subheader("Top Local Competitors")
        if 'competitors' in st.session_state and not st.session_state['competitors'].empty:
            st.dataframe(st.session_state['competitors'].head(10), use_container_width=True)
            
            # Competitor visualization
            st.subheader("Competitor Presence")
            top5 = st.session_state['competitors'].head(5)
            
            # Create bar chart
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=top5['business_name'],
                y=top5['appearance_count'],
                text=top5['appearance_count'],
                textposition='auto',
                marker_color='lightseagreen'
            ))
            fig.update_layout(
                title='Top 5 Competitors by Grid Presence',
                xaxis_title='Business',
                yaxis_title='Appearances in Grid',
                template='plotly_white'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Ratings comparison
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(
                x=top5['business_name'],
                y=top5['avg_rating'],
                text=top5['avg_rating'].apply(lambda x: f"{x:.1f}"),
                textposition='auto',
                marker_color='coral'
            ))
            fig2.update_layout(
                title='Average Rating of Top Competitors',
                xaxis_title='Business',
                yaxis_title='Average Rating',
                template='plotly_white',
                yaxis=dict(range=[0, 5])
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No competitor data available. Run a scan to analyze competitors.")
    
    # Downloads
    st.header("Export Results")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button('Download CSV', df.to_csv(index=False), 'geo_grid_results.csv', key='csv')
    with col2:
        st.download_button('Download JSON', json.dumps(st.session_state['data']), 'geo_grid_results.json', key='json')

    # Help info
    with st.expander("How to Interpret Results"):
        st.markdown("""
        ### Understanding Your Geo-Grid Results
        
        - **Local Pack Coverage**: Shows where your business appears in Google's Local Pack (the map listings at the top of search results). Green markers (1-3) are excellent positions.
        
        - **Organic Coverage**: The heatmap shows where your website ranks in the organic search results. Brighter areas indicate better rankings (positions 1-3).
        
        - **Maps Coverage**: Shows your business's ranking in Google Maps results at each grid point. Green markers indicate top 3 positions.
        
        - **Competitor Analysis**: Identifies which competitors appear most frequently across your grid, helping you understand your competition landscape.
        
        ### Improvement Strategies
        
        1. **For Local Pack**: Focus on optimizing your Google Business Profile with accurate information, photos, and collecting more reviews.
        
        2. **For Organic Rankings**: Improve on-page SEO for your target keywords and build quality local backlinks.
        
        3. **For Maps Visibility**: Ensure NAP (Name, Address, Phone) consistency across all online directories and embed Google Maps on your website.
        """)

st.markdown('---')
st.write('© Built with Serpstack, ScraperAPI & Google Maps API')
