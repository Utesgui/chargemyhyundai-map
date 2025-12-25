"""Discover all available tariffs"""
import requests
import json

session = requests.Session()
session.headers.update({
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://chargemyhyundai.com',
    'Referer': 'https://chargemyhyundai.com/web/de/hyundai-de/map'
})

# Get all tariffs
response = session.get('https://chargemyhyundai.com/api/map/v1/de/tariffs?locale=de_DE')
tariffs = response.json()

print("=" * 60)
print("Available Tariffs:")
print("=" * 60)

for t in tariffs:
    print(f"\nTariff ID: {t['id']}")
    print(f"  Name: {t['name']}")
    print(f"  Expired: {t.get('expired', False)}")
    
    # Fixed fees
    if 'fixedFees' in t:
        ff = t['fixedFees']
        if 'baseFee' in ff:
            print(f"  Base Fee: {ff['baseFee'].get('prices', [{}])[0].get('price', 'N/A')}")
        if 'activationFee' in ff:
            print(f"  Activation Fee: {ff['activationFee'].get('prices', [{}])[0].get('price', 'N/A')}")
    
    # Session fees
    if 'chargingFees' in t:
        cf = t['chargingFees']
        if 'ac' in cf and 'sessionFees' in cf['ac'] and cf['ac']['sessionFees']:
            print(f"  AC Session Fee: {cf['ac']['sessionFees'][0].get('price', 'N/A')}")
        if 'dc' in cf and 'sessionFees' in cf['dc'] and cf['dc']['sessionFees']:
            print(f"  DC Session Fee: {cf['dc']['sessionFees'][0].get('price', 'N/A')}")

# Test pricing with Smart tariff
print("\n" + "=" * 60)
print("Testing Smart Tariff Pricing:")
print("=" * 60)

# Use a known charge point
charge_point_id = "DE:DCS:CHARGE_POINT:f82a935f-bd6c-3f69-aa6c-8abf9ce763af"

for tariff_id in [t['id'] for t in tariffs if not t.get('expired', False)]:
    try:
        resp = session.post(
            f'https://chargemyhyundai.com/api/map/v1/de/tariffs/{tariff_id}/prices',
            json=[{"charge_point": charge_point_id, "power_type": "AC", "power": 11}]
        )
        if resp.status_code == 200:
            data = resp.json()[0]
            energy = next((c['price'] for e in data['elements'] for c in e['price_components'] if c['type'] == 'ENERGY'), None)
            flat = next((c['price'] for e in data['elements'] for c in e['price_components'] if c['type'] == 'FLAT'), None)
            print(f"\n{tariff_id}:")
            print(f"  Energy: {energy} EUR/kWh")
            print(f"  Session Fee: {flat} EUR")
        else:
            print(f"\n{tariff_id}: Status {resp.status_code}")
    except Exception as e:
        print(f"\n{tariff_id}: Error - {e}")
