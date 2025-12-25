"""
ChargeMyHyundai Price Map - Flask Backend
A web application to display charging station prices on an interactive map.
"""

from flask import Flask, jsonify, request, render_template, send_from_directory
from flask_cors import CORS
import requests
from functools import lru_cache
from datetime import datetime, timedelta
import time
import os

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
    """Get detailed pool information including CPO names and addresses"""
    try:
        data = request.json
        pool_ids = data.get('pool_ids', [])
        market = data.get('market', DEFAULT_MARKET)
        
        if not pool_ids:
            return jsonify({'error': 'No pool IDs provided'}), 400
        
        # Check cache first
        cached_results = {}
        uncached_ids = []
        for pid in pool_ids:
            if pid in pool_cache:
                cached_results[pid] = pool_cache[pid]
            else:
                uncached_ids.append(pid)
        
        # Fetch uncached pools in batches
        fetched_results = {}
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
                        
                        # Get max power level and plug types from all connectors
                        max_power = 0
                        plug_types = set()
                        charging_stations = pool.get('chargingStations', [])
                        for station in charging_stations:
                            for cp in station.get('chargePoints', []):
                                for connector in cp.get('connectors', []):
                                    power_level = connector.get('powerLevel', 0)
                                    if power_level and power_level > max_power:
                                        max_power = power_level
                                    plug_type = connector.get('plugType')
                                    if plug_type:
                                        plug_types.add(plug_type)
                        
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
                            'plug_types': list(plug_types)
                        }
                        
                        # Cache it
                        pool_cache[pool_id] = pool_info
                        fetched_results[pool_id] = pool_info
        
        # Combine results
        all_results = {**cached_results, **fetched_results}
        return jsonify(all_results)
        
    except Exception as e:
        import traceback
        print(f"Error in api_pool_details: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/prices', methods=['POST'])
def api_prices():
    """Get prices for multiple charge points"""
    try:
        data = request.json
        charge_points = data.get('charge_points', [])
        tariff_id = data.get('tariff_id', 'HYUNDAI_FLEX')
        power_type = data.get('power_type', 'AC')
        power = data.get('power', 11)
        market = data.get('market', DEFAULT_MARKET)
        
        if not charge_points:
            return jsonify({'error': 'No charge points provided'}), 400
        
        # Limit batch size to avoid rate limiting
        charge_points = charge_points[:10]
        
        # Build request payload
        payload = [
            {
                "charge_point": cp,
                "power_type": power_type,
                "power": power
            }
            for cp in charge_points
        ]
        
        print(f"Requesting prices for {len(charge_points)} charge points, tariff={tariff_id}, market={market}")
        
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
        
        response = temp_session.post(
            f"{BASE_URL}/{market}/tariffs/{tariff_id}/prices",
            json=payload
        )
        
        if not response.ok:
            print(f"API Error: {response.status_code}")
            # Return empty prices instead of failing
            return jsonify([
                {
                    'charge_point': cp,
                    'power_type': power_type,
                    'power': power,
                    'currency': 'EUR',
                    'energy_price': None,
                    'session_fee': None,
                    'blocking_fee': None,
                    'blocking_after_minutes': None
                }
                for cp in charge_points
            ])
        
        # Parse and simplify response
        prices = []
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
            
            prices.append({
                'charge_point': item.get('price_identifier', {}).get('charge_point', ''),
                'power_type': item.get('price_identifier', {}).get('power_type', power_type),
                'power': item.get('price_identifier', {}).get('power', power),
                'currency': currency,
                'energy_price': energy_price,
                'session_fee': session_fee,
                'blocking_fee': blocking_fee,
                'blocking_after_minutes': blocking_after
            })
        
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
    print("=" * 60)
    
    app.run(debug=True, port=5000)
