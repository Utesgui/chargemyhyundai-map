# ChargeMyHyundai Price Map

An interactive web application to visualize charging station prices from the ChargeMyHyundai network on OpenStreetMap.

## Features

- 🗺️ **Interactive OpenStreetMap** - Dark themed map with price markers
- 💰 **Price Visualization** - Color-coded markers showing €/kWh prices
  - 🟢 Green: < 0.45€/kWh (cheap)
  - 🟡 Orange: 0.45€ - 0.60€/kWh (moderate)
  - 🔴 Red: > 0.60€/kWh (expensive)
- 🔄 **Tariff Toggle** - Switch between Flex and Smart tariffs
  - **Flex**: 0€/month, higher per-kWh rates
  - **Smart**: 9.99€/month, lower per-kWh rates
- ⚡ **Power Type Filter** - Toggle between AC and DC charging
- 💱 **Max Price Filter** - Slider to hide stations above a certain price
- 📋 **List View** - Sortable list of stations with prices
- 🔍 **Address Search** - Search for locations via OpenStreetMap Nominatim
- 📊 **Statistics** - Station count, average price, minimum price

## Installation

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   ```

2. Activate the virtual environment:
   - Windows: `.\.venv\Scripts\activate`
   - Linux/Mac: `source .venv/bin/activate`

3. Install dependencies:
   ```bash
   pip install flask flask-cors requests
   ```

4. Run the application:
   ```bash
   python app.py
   ```

5. Open http://localhost:5000 in your browser

## Project Structure

```
chargemyhyundai/
├── app.py                      # Flask backend server
├── chargemyhyundai_api.py      # Python API client library
├── CHARGEMYHYUNDAI_API.md      # API documentation
├── README.md                   # This file
├── discover_tariffs.py         # Script to discover tariffs
├── templates/
│   └── index.html              # Web application frontend
└── static/                     # Static assets (CSS, JS)
```

## API Documentation

See [CHARGEMYHYUNDAI_API.md](CHARGEMYHYUNDAI_API.md) for complete API documentation.

### Available Tariffs

| Tariff | Monthly Fee | Activation Fee | AC Price | DC Price | Session Fee (AC) |
|--------|-------------|----------------|----------|----------|------------------|
| HYUNDAI_FLEX | 0€ | 7.49€ | ~0.48€/kWh | ~0.72€/kWh | 0.74€ |
| HYUNDAI_SMART | 9.99€ | 0€ | ~0.43€/kWh | ~0.68€/kWh | 0.13€ |

*Note: Prices vary by charging station and CPO*

## Python API Client

You can also use the API client directly:

```python
from chargemyhyundai_api import ChargeMyHyundaiAPI

api = ChargeMyHyundaiAPI()

# Find stations near Berlin
stations = api.find_stations(52.52, 13.405, radius_km=1.0)

# Get price for a specific charge point
price = api.get_price(
    "DE:DCS:CHARGE_POINT:xxxx",
    power_type="AC",
    power_kw=11,
    tariff_id="HYUNDAI_FLEX"
)

print(price)  # Energy: 0.48 EUR/kWh | Session Fee: 0.74 EUR
```

## Rate Limiting

The ChargeMyHyundai API has rate limiting. The web application uses small batches (5 charge points at a time) with delays to avoid 403 errors.

## Disclaimer

This is an unofficial tool created by reverse-engineering the public ChargeMyHyundai API. It is not affiliated with Hyundai or Digital Charging Solutions GmbH. Use at your own risk.

## License

MIT
