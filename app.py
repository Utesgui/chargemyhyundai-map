"""
ChargeMyHyundai Price Map - Flask Backend
A web application to display charging station prices on an interactive map.

Features:
- SQLite-based caching for station data and prices
- Background updates with rate limiting (3 requests per 10 seconds)
- Manual refresh button for individual stations
- 24-hour cache expiry with automatic daily updates
"""

from flask import Flask, jsonify, request, render_template, send_from_directory
from flask_cors import CORS
import requests
from functools import lru_cache
from datetime import datetime, timedelta
import time
import os
import atexit

# Import caching and background updater
from station_cache import get_cache, StationCache
from background_updater import init_updater, get_updater, BackgroundUpdater

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# API Configuration
BASE_URL = "https://chargemyhyundai.com/api/map/v1"
DEFAULT_MARKET = "de"
DEFAULT_LOCALE = "de_DE"

# CPO cache (operator names)
cpo_cache = {}
cpo_cache_time = None

# Session with proper headers
session = requests.Session()
session.headers.update({
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://chargemyhyundai.com',
    'Referer': 'https://chargemyhyundai.com/web/de/hyundai-de/map'
})

# Cache for tariffs (refreshed every hour)
tariff_cache = {}
tariff_cache_time = None

# Initialize station cache
station_cache = get_cache()

# Initialize and start background updater
background_updater = init_updater(session, base_url=BASE_URL, default_market=DEFAULT_MARKET)


def start_background_updater():
    """Start the background updater (called after app initialization)"""
    if background_updater and not background_updater.is_running():
        background_updater.start()
        print("📦 Background cache updater started")


def stop_background_updater():
    """Stop the background updater on shutdown"""
    if background_updater and background_updater.is_running():
        background_updater.stop()
        print("📦 Background cache updater stopped")


# Register shutdown handler
atexit.register(stop_background_updater)


def get_tariffs(market=DEFAULT_MARKET):
    """Get tariffs with caching"""
    global tariff_cache, tariff_cache_time
    
    cache_key = market
    if cache_key in tariff_cache and tariff_cache_time:
        if datetime.now() - tariff_cache_time < timedelta(hours=1):
            return tariff_cache[cache_key]
    
    response = session.get(
        f"{BASE_URL}/{market}/tariffs",
        params={"locale": DEFAULT_LOCALE}
    )
    response.raise_for_status()
    tariffs = response.json()
    
    tariff_cache[cache_key] = tariffs
    tariff_cache_time = datetime.now()
    
    return tariffs


@app.route('/')
def index():
    """Serve the main application"""
    return render_template('index.html')


@app.route('/api/tariffs')
def api_tariffs():
    """Get available tariffs"""
    try:
        tariffs = get_tariffs()
        simplified = []
        for t in tariffs:
            if not t.get('expired', False):
                simplified.append({
                    'id': t['id'],
                    'name': t['name'],
                    'baseFee': t.get('fixedFees', {}).get('baseFee', {}).get('prices', [{}])[0].get('price', 'N/A'),
                    'activationFee': t.get('fixedFees', {}).get('activationFee', {}).get('prices', [{}])[0].get('price', 'N/A')
                })
        return jsonify(simplified)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cached-stations')
def api_cached_stations():
    """
    Get all cached stations for fast initial display.
    Returns stations from the local cache without hitting the external API.
    """
    try:
        market = request.args.get('market', DEFAULT_MARKET)
        lat_nw = request.args.get('lat_nw')
        lng_nw = request.args.get('lng_nw')
        lat_se = request.args.get('lat_se')
        lng_se = request.args.get('lng_se')
        
        if lat_nw and lng_nw and lat_se and lng_se:
            # Get stations in bounding box
            cached = station_cache.get_stations_in_bounds(
                float(lat_nw), float(lng_nw), 
                float(lat_se), float(lng_se),
                market
            )
        else:
            # Get all stations
            cached = station_cache.get_all_stations(market)
        
        # Transform to format expected by frontend
        stations = []
        for s in cached:
            # Build chargePoints array from cached data
            charge_points = []
            for cp_id in (s.get('charge_points_ac') or []):
                charge_points.append({'id': cp_id, 'powerType': 'AC'})
            for cp_id in (s.get('charge_points_dc') or []):
                charge_points.append({'id': cp_id, 'powerType': 'DC'})
            
            stations.append({
                'id': s['pool_id'],
                'latitude': s['latitude'],
                'longitude': s['longitude'],
                'chargePointCount': s.get('charge_point_count') or len(charge_points),
                'dcsTcpoId': s.get('cpo_id'),
                'chargePoints': charge_points,
                'maxPower': s.get('max_power'),
                'plugTypes': s.get('plug_types') or [],
                'cached': True,
                'cpoName': s.get('cpo_name'),
                'locationName': s.get('location_name'),
                'city': s.get('city'),
                'street': s.get('street')
            })
        
        return jsonify({
            'stations': stations,
            'count': len(stations),
            'source': 'cache'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'stations': [], 'count': 0}), 500


@app.route('/api/stations')
def api_stations():
    """Find charging stations in a bounding box"""
    try:
        lat_nw = float(request.args.get('lat_nw'))
        lng_nw = float(request.args.get('lng_nw'))
        lat_se = float(request.args.get('lat_se'))
        lng_se = float(request.args.get('lng_se'))
        precision = int(request.args.get('precision', 10))
        market = request.args.get('market', DEFAULT_MARKET)
        
        payload = {
            "searchCriteria": {
                "latitudeNW": lat_nw,
                "longitudeNW": lng_nw,
                "latitudeSE": lat_se,
                "longitudeSE": lng_se,
                "precision": precision,
                "unpackSolitudeCluster": True,
                "unpackClustersWithSinglePool": True
            },
            "withChargePointIds": True,
            "filterCriteria": {
                "authenticationMethods": [],
                "cableAttachedTypes": [],
                "paymentMethods": [],
                "plugTypes": [],
                "poolLocationTypes": [],
                "valueAddedServices": [],
                "dcsTcpoIds": []
            }
        }
        
        response = session.post(
            f"{BASE_URL}/{market}/query",
            json=payload,
            headers={"rest-api-path": "clusters"}
        )
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/markets')
def api_markets():
    """Get all available markets"""
    try:
        response = session.get(
            f"{BASE_URL}/{DEFAULT_MARKET}/markets",
            params={"locale": DEFAULT_LOCALE}
        )
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cpo/<cpo_id>')
def api_cpo_info(cpo_id):
    """Get CPO information"""
    global cpo_cache, cpo_cache_time
    
    # Check cache
    if cpo_id in cpo_cache:
        return jsonify(cpo_cache[cpo_id])
    
    try:
        # Try to get CPO info from the API
        response = session.get(
            f"{BASE_URL}/{DEFAULT_MARKET}/cpo/{cpo_id}",
            params={"locale": DEFAULT_LOCALE}
        )
        if response.ok:
            data = response.json()
            cpo_cache[cpo_id] = data
            return jsonify(data)
        else:
            # Return a placeholder if not found
            return jsonify({'id': cpo_id, 'name': 'Unknown Operator'})
    except Exception as e:
        return jsonify({'id': cpo_id, 'name': 'Unknown Operator'})


# Pool details cache
pool_cache = {}


@app.route('/api/pool-details', methods=['POST'])
def api_pool_details():
    """Get detailed pool information including CPO names and addresses.
    Uses SQLite cache for faster responses and reduced API load.
    """
    try:
        data = request.json
        pool_ids = data.get('pool_ids', [])
        market = data.get('market', DEFAULT_MARKET)
        
        if not pool_ids:
            return jsonify({'error': 'No pool IDs provided'}), 400
        
        # Check SQLite cache first
        cached_results = station_cache.get_stations(pool_ids)
        
        # Convert cached results to expected format
        results = {}
        for pool_id, station in cached_results.items():
            results[pool_id] = {
                'pool_id': pool_id,
                'cpo_name': station.get('cpo_name', 'Unbekannt'),
                'location_name': station.get('location_name'),
                'street': station.get('street'),
                'city': station.get('city'),
                'zip_code': station.get('zip_code'),
                'max_power': station.get('max_power'),
                'plug_types': station.get('plug_types', []),
                'charge_points_ac': station.get('charge_points_ac', []),
                'charge_points_dc': station.get('charge_points_dc', []),
                'contact_name': station.get('contact_name'),
                'contact_phone': station.get('contact_phone'),
                'cached': True,
                'updated_at': station.get('updated_at')
            }
        
        # Find IDs that need to be fetched from API
        uncached_ids = [pid for pid in pool_ids if pid not in cached_results]
        
        # Also check in-memory cache for backward compatibility
        for pid in list(uncached_ids):
            if pid in pool_cache:
                results[pid] = pool_cache[pid]
                results[pid]['cached'] = True
                uncached_ids.remove(pid)
        
        # Fetch uncached pools in batches
        if uncached_ids:
            # Limit batch size
            batch_size = 20
            for i in range(0, len(uncached_ids), batch_size):
                batch = uncached_ids[i:i + batch_size]
                
                response = session.post(
                    f"{BASE_URL}/{market}/query",
                    json={"dcsPoolIds": batch},
                    headers={"rest-api-path": "pools"}
                )
                
                if response.ok:
                    pools_data = response.json()
                    for pool in pools_data:
                        pool_id = pool.get('dcsPoolId')
                        if not pool_id:
                            continue
                        
                        # Extract useful information
                        cpo_name = pool.get('technicalChargePointOperatorName', 'Unbekannt')
                        
                        # Get location details
                        location_name = None
                        street = None
                        city = None
                        zip_code = None
                        
                        locations = pool.get('poolLocations', [])
                        if locations:
                            loc = locations[0]
                            street = loc.get('street')
                            city = loc.get('city')
                            zip_code = loc.get('zipCode')
                            
                            loc_names = loc.get('poolLocationNames', [])
                            if loc_names:
                                location_name = loc_names[0].get('name')
                        
                        # Get contact info
                        contact_name = None
                        contact_phone = None
                        contacts = pool.get('poolContacts', [])
                        if contacts:
                            contact_name = contacts[0].get('name')
                            contact_phone = contacts[0].get('phone')
                        
                        # Get max power level, plug types, and charge points by AC/DC
                        max_power = 0
                        plug_types = set()
                        charge_points_by_type = {'AC': [], 'DC': []}
                        charging_stations = pool.get('chargingStations', [])
                        for station in charging_stations:
                            for cp in station.get('chargePoints', []):
                                cp_id = cp.get('dcsCpId')
                                if not cp_id:
                                    continue
                                for connector in cp.get('connectors', []):
                                    power_level = connector.get('powerLevel', 0)
                                    if power_level and power_level > max_power:
                                        max_power = power_level
                                    plug_type = connector.get('plugType', '')
                                    if plug_type:
                                        plug_types.add(plug_type)
                                    
                                    # Classify by connector type first, fallback to phaseType
                                    plug_upper = plug_type.upper()
                                    if 'TYP2' in plug_upper or 'TYPE2' in plug_upper or 'TYPE 2' in plug_upper:
                                        cp_type = 'AC'
                                    elif 'CCS' in plug_upper or 'COMBO' in plug_upper:
                                        cp_type = 'DC'
                                    else:
                                        # Fallback to phaseType
                                        cp_type = connector.get('phaseType', 'AC')
                                    
                                    # Add to list if not already there
                                    if cp_id not in charge_points_by_type[cp_type]:
                                        charge_points_by_type[cp_type].append(cp_id)
                        
                        pool_info = {
                            'pool_id': pool_id,
                            'cpo_name': cpo_name,
                            'location_name': location_name,
                            'street': street,
                            'city': city,
                            'zip_code': zip_code,
                            'contact_name': contact_name,
                            'contact_phone': contact_phone,
                            'max_power': max_power,
                            'plug_types': list(plug_types),
                            'charge_points_ac': charge_points_by_type.get('AC', []),
                            'charge_points_dc': charge_points_by_type.get('DC', []),
                            'cached': False,
                            'updated_at': datetime.utcnow().isoformat()
                        }
                        
                        # Save to SQLite cache
                        station_cache.save_station(pool_id, market, pool_info)
                        
                        # Also cache in memory for backward compatibility
                        pool_cache[pool_id] = pool_info
                        results[pool_id] = pool_info
        
        return jsonify(results)
        
    except Exception as e:
        import traceback
        print(f"Error in api_pool_details: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/cached-prices', methods=['POST'])
def api_cached_prices():
    """
    Get cached prices for multiple stations (all tariffs, all power types).
    This is used for fast tariff switching without API calls.
    """
    try:
        data = request.json
        pool_ids = data.get('pool_ids', [])
        market = data.get('market', DEFAULT_MARKET)
        
        if not pool_ids:
            return jsonify({})
        
        # Get all cached prices for these pools
        all_prices = station_cache.get_all_prices_for_pools(pool_ids[:200], market)
        
        return jsonify(all_prices)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/prices', methods=['POST'])
def api_prices():
    """Get prices for multiple charge points.
    Uses SQLite cache for faster responses when data is fresh.
    """
    try:
        data = request.json
        charge_points = data.get('charge_points', [])
        pool_ids = data.get('pool_ids', [])  # New: accept pool IDs for cache lookup
        tariff_id = data.get('tariff_id', 'HYUNDAI_FLEX')
        power_type = data.get('power_type', 'AC')
        power = data.get('power', 11)
        market = data.get('market', DEFAULT_MARKET)
        
        if not charge_points:
            return jsonify({'error': 'No charge points provided'}), 400
        
        # Limit batch size to avoid rate limiting
        charge_points = charge_points[:10]
        pool_ids = pool_ids[:10] if pool_ids else []
        
        # Check SQLite cache first for cached prices
        cached_prices = {}
        if pool_ids:
            cached_prices = station_cache.get_prices(pool_ids, tariff_id, power_type, market)
        
        # Build result list
        prices = []
        uncached_cps = []
        uncached_pool_map = {}  # charge_point -> pool_id
        
        for i, cp in enumerate(charge_points):
            pool_id = pool_ids[i] if i < len(pool_ids) else None
            
            # Check if we have a cached price for this pool
            if pool_id and pool_id in cached_prices:
                cached = cached_prices[pool_id]
                prices.append({
                    'charge_point': cp,
                    'power_type': power_type,
                    'power': power,
                    'currency': cached.get('currency', 'EUR'),
                    'energy_price': cached.get('energy_price'),
                    'session_fee': cached.get('session_fee'),
                    'blocking_fee': cached.get('blocking_fee'),
                    'blocking_after_minutes': cached.get('blocking_after_minutes'),
                    'cached': True,
                    'updated_at': cached.get('updated_at')
                })
            else:
                uncached_cps.append(cp)
                if pool_id:
                    uncached_pool_map[cp] = pool_id
        
        # Fetch uncached prices from API
        if uncached_cps:
            print(f"Requesting prices for {len(uncached_cps)} charge points, tariff={tariff_id}, market={market}")
            
            # Create fresh session for each request to avoid cookie issues
            import requests as req
            temp_session = req.Session()
            temp_session.headers.update({
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Origin': 'https://chargemyhyundai.com',
                'Referer': 'https://chargemyhyundai.com/web/de/hyundai-de/map'
            })
            
            payload = [
                {
                    "charge_point": cp,
                    "power_type": power_type,
                    "power": power
                }
                for cp in uncached_cps
            ]
            
            response = temp_session.post(
                f"{BASE_URL}/{market}/tariffs/{tariff_id}/prices",
                json=payload
            )
            
            if not response.ok:
                print(f"API Error: {response.status_code}")
                # Return empty prices for uncached items
                for cp in uncached_cps:
                    prices.append({
                        'charge_point': cp,
                        'power_type': power_type,
                        'power': power,
                        'currency': 'EUR',
                        'energy_price': None,
                        'session_fee': None,
                        'blocking_fee': None,
                        'blocking_after_minutes': None,
                        'cached': False
                    })
            else:
                # Parse and save response
                response_data = response.json()
                
                for item in response_data:
                    energy_price = None
                    session_fee = None
                    blocking_fee = None
                    blocking_after = None
                    currency = item.get('currency', 'EUR')
                    
                    for element in item.get('elements', []):
                        for component in element.get('price_components', []):
                            if component['type'] == 'ENERGY':
                                energy_price = component['price']
                            elif component['type'] == 'FLAT':
                                session_fee = component['price']
                            elif component['type'] == 'TIME':
                                blocking_fee = component['price']
                                min_duration = element.get('restrictions', {}).get('min_duration')
                                if min_duration:
                                    blocking_after = min_duration // 60
                    
                    cp_id = item.get('price_identifier', {}).get('charge_point', '')
                    
                    price_data = {
                        'charge_point': cp_id,
                        'power_type': item.get('price_identifier', {}).get('power_type', power_type),
                        'power': item.get('price_identifier', {}).get('power', power),
                        'currency': currency,
                        'energy_price': energy_price,
                        'session_fee': session_fee,
                        'blocking_fee': blocking_fee,
                        'blocking_after_minutes': blocking_after,
                        'cached': False,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                    
                    prices.append(price_data)
                    
                    # Save to SQLite cache if we have the pool ID
                    pool_id = uncached_pool_map.get(cp_id)
                    if pool_id and energy_price is not None:
                        station_cache.save_price(
                            pool_id, cp_id, tariff_id, power_type, power, market, price_data
                        )
        
        return jsonify(prices)
    except Exception as e:
        import traceback
        print(f"Error in api_prices: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/status', methods=['POST'])
def api_status():
    """Get charge point availability status"""
    try:
        data = request.json
        charge_point_ids = data.get('charge_point_ids', [])
        
        if not charge_point_ids:
            return jsonify({'error': 'No charge point IDs provided'}), 400
        
        payload = {
            "DCSChargePointDynStatusRequest": [
                {"dcsChargePointId": cp_id} for cp_id in charge_point_ids
            ]
        }
        
        response = session.post(
            f"{BASE_URL}/{DEFAULT_MARKET}/query",
            json=payload,
            headers={"rest-api-path": "charge-points"}
        )
        response.raise_for_status()
        
        result = response.json()
        statuses = {}
        for item in result.get('DCSChargePointDynStatusResponse', []):
            statuses[item['dcsChargePointId']] = {
                'status': item.get('OperationalStateCP', 'UNKNOWN'),
                'timestamp': item.get('Timestamp')
            }
        
        return jsonify(statuses)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/operators')
def api_operators():
    """Get list of charge point operators (CPOs)"""
    # This would need to be extracted from station data
    # For now, return a placeholder
    return jsonify([])


@app.route('/api/cache/stats')
def api_cache_stats():
    """Get cache statistics and background updater status"""
    try:
        cache_stats = station_cache.get_stats()
        updater_status = background_updater.get_status() if background_updater else {}
        
        return jsonify({
            'cache': cache_stats,
            'updater': updater_status
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache/refresh', methods=['POST'])
def api_cache_refresh():
    """
    Force refresh a specific station's data.
    Called when user clicks the refresh button in station popup.
    """
    try:
        data = request.json
        pool_id = data.get('pool_id')
        market = data.get('market', DEFAULT_MARKET)
        
        if not pool_id:
            return jsonify({'error': 'No pool_id provided'}), 400
        
        if not background_updater:
            return jsonify({'error': 'Background updater not initialized'}), 500
        
        # Force update the station
        try:
            updated_station = background_updater.force_update(pool_id, market)
            
            # Also get updated prices
            updated_prices = {}
            for tariff_id in ['HYUNDAI_FLEX', 'HYUNDAI_SMART']:
                for power_type in ['AC', 'DC']:
                    price = station_cache.get_price(pool_id, tariff_id, power_type, market)
                    if price:
                        key = f"{tariff_id}_{power_type}"
                        updated_prices[key] = price
            
            return jsonify({
                'success': True,
                'station': updated_station,
                'prices': updated_prices,
                'message': 'Station data refreshed successfully'
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'message': 'Failed to refresh station data'
            }), 429 if 'rate limit' in str(e).lower() else 500
        
    except Exception as e:
        import traceback
        print(f"Error in api_cache_refresh: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache/queue', methods=['POST'])
def api_cache_queue():
    """
    Add a station to the update queue for background update.
    Lower priority than manual refresh, but will be updated eventually.
    """
    try:
        data = request.json
        pool_id = data.get('pool_id')
        market = data.get('market', DEFAULT_MARKET)
        priority = data.get('priority', 5)  # Default medium priority
        
        if not pool_id:
            return jsonify({'error': 'No pool_id provided'}), 400
        
        station_cache.queue_update(pool_id, market, priority)
        
        return jsonify({
            'success': True,
            'message': 'Station queued for update',
            'queue_size': station_cache.get_queue_size()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Create templates and static directories if they don't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)
    
    print("=" * 60)
    print("ChargeMyHyundai Price Map")
    print("=" * 60)
    print("Starting server at http://localhost:5000")
    
    # Show cache stats
    stats = station_cache.get_stats()
    print(f"📦 Cache: {stats['total_stations']} stations, {stats['total_prices']} prices")
    print(f"   Fresh: {stats['fresh_stations']}, Stale: {stats['stale_stations']}")
    
    # Start background updater
    start_background_updater()
    
    print("=" * 60)
    
    app.run(debug=True, port=5000, use_reloader=False)  # Disable reloader to prevent double background threads
