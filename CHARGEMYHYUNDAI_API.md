# ChargeMyHyundai API Documentation

**Base URL:** `https://chargemyhyundai.com`

## Overview

ChargeMyHyundai is a charging network service by Digital Charging Solutions GmbH for Hyundai electric vehicle owners. This document describes the publicly accessible API endpoints discovered through reverse engineering.

## Authentication

Most endpoints work without authentication. For personalized pricing (based on user's contract), login is required, but the Flex tariff prices are accessible publicly.

### Required Headers

The API requires browser-like headers to prevent blocking:

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Origin: https://chargemyhyundai.com
Referer: https://chargemyhyundai.com/web/de/hyundai-de/map
Accept: application/json, text/plain, */*
Content-Type: application/json
```

---

## API Endpoints

### 1. Initialize Map Configuration

Get map configuration and feature flags.

```
GET /api/map/v1/{market}/init?locale={locale}
```

**Parameters:**
- `market`: Country code (e.g., `de` for Germany)
- `locale`: Locale string (e.g., `de_DE`)

**Example:**
```bash
curl "https://chargemyhyundai.com/api/map/v1/de/init?locale=de_DE"
```

**Response:**
```json
{
  "config": {
    "mapsApiUrl": "/api/map/v1/de",
    "customerApiUrl": "/api/dashboard/v1/de",
    "publicApiUrl": "/api/public/v1/de",
    "market": "DE",
    "oem": "HYUNDAI",
    "features": ["PRICING", "CPO_LOGOS", "POOL_RATING", "CPO_FILTER", "TARIFF_BASED_PRICING", "PNC"],
    "signedIn": false
  }
}
```

---

### 2. Get Available Markets

List all available countries/markets with charge point counts.

```
GET /api/map/v1/{market}/markets?locale={locale}
```

**Example:**
```bash
curl "https://chargemyhyundai.com/api/map/v1/de/markets?locale=de_DE"
```

**Response:**
```json
[
  {"countryCode": "AT", "oem": "HYUNDAI", "numberOfChargePoints": 32276},
  {"countryCode": "BE", "oem": "HYUNDAI", "numberOfChargePoints": 108978},
  {"countryCode": "DE", "oem": "HYUNDAI", "numberOfChargePoints": 193161},
  // ... more countries
]
```

---

### 3. Get Tariffs

Get available tariff plans and their structure.

```
GET /api/map/v1/{market}/tariffs?locale={locale}
```

**Example:**
```bash
curl "https://chargemyhyundai.com/api/map/v1/de/tariffs?locale=de_DE"
```

**Response (excerpt):**
```json
[
  {
    "id": "HYUNDAI_FLEX",
    "name": "Flex",
    "expired": false,
    "fixedFees": {
      "baseFee": {"label": "Grundgebühr", "prices": [{"price": "0,00 EUR / Monat"}]},
      "activationFee": {"label": "Aktivierungsgebühr", "prices": [{"price": "7,49 EUR"}]}
    },
    "chargingFees": {
      "ac": {
        "header": "AC-Laden",
        "cpoBasedPricing": true,
        "sessionFees": [{"price": "0,59 EUR"}]
      },
      "dc": {
        "header": "DC-Laden",
        "cpoBasedPricing": true,
        "sessionFees": [{"price": "0,59 EUR"}]
      }
    },
    "packages": [
      {
        "code": "IONITY_D_V1",
        "header": "IONITY Premium",
        "fixedFees": {"baseFee": {"value": "6,99 EUR / Monat"}}
      }
    ]
  }
]
```

---

### 4. Query Charging Stations (Clusters/Pools)

Find charging stations in a geographic area. **This is the main endpoint for station discovery.**

```
POST /api/map/v1/{market}/query
```

**Required Headers:**
```
Content-Type: application/json
Accept: application/json
rest-api-path: clusters
```

**Request Body:**
```json
{
  "searchCriteria": {
    "latitudeNW": 52.52,           // Northwest corner latitude
    "longitudeNW": 13.38,          // Northwest corner longitude
    "latitudeSE": 52.51,           // Southeast corner latitude  
    "longitudeSE": 13.42,          // Southeast corner longitude
    "precision": 10,               // Higher = more detail (6-10 recommended)
    "unpackSolitudeCluster": true,
    "unpackClustersWithSinglePool": true
  },
  "withChargePointIds": true,      // Include charge point IDs in response
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
```

**Example:**
```bash
curl -X POST "https://chargemyhyundai.com/api/map/v1/de/query" \
  -H "Content-Type: application/json" \
  -H "rest-api-path: clusters" \
  -d '{
    "searchCriteria": {
      "latitudeNW": 52.52,
      "longitudeNW": 13.38,
      "latitudeSE": 52.51,
      "longitudeSE": 13.42,
      "precision": 10,
      "unpackSolitudeCluster": true,
      "unpackClustersWithSinglePool": true
    },
    "withChargePointIds": true,
    "filterCriteria": {
      "authenticationMethods": [],
      "cableAttachedTypes": [],
      "paymentMethods": [],
      "plugTypes": [],
      "poolLocationTypes": [],
      "valueAddedServices": [],
      "dcsTcpoIds": []
    }
  }'
```

**Response:**
```json
{
  "poolClusters": [],  // Clusters when zoomed out
  "pools": [
    {
      "id": "DE:DCS:POOL:0f02b3e8-74e6-3ad2-9bdd-56fff6a52b9d",
      "longitude": 13.393325805664062,
      "latitude": 52.51645278930664,
      "dcsTechnicalChargePointOperatorId": "DE:DCS:TECH_CHARGE_POINT_OPERATOR:2ad1254d-ceb8-36fd-b8ae-0516bd295db4",
      "preferredPartnerStatus": false,
      "chargePointCount": 20,
      "chargePoints": [
        {"id": "DE:DCS:CHARGE_POINT:00ed1077-a85f-3539-a13e-11548ebe755a"},
        {"id": "DE:DCS:CHARGE_POINT:02024052-598d-3d04-922f-cc261c186cd9"},
        // ... more charge points
      ]
    }
  ]
}
```

---

### 5. Get Charge Point Status

Check real-time availability of charge points.

```
POST /api/map/v1/{market}/query
```

**Required Headers:**
```
Content-Type: application/json
Accept: application/json
rest-api-path: charge-points
```

**Request Body:**
```json
{
  "DCSChargePointDynStatusRequest": [
    {"dcsChargePointId": "DE:DCS:CHARGE_POINT:00ed1077-a85f-3539-a13e-11548ebe755a"}
  ]
}
```

**Response:**
```json
{
  "DCSChargePointDynStatusResponse": [
    {
      "dcsChargePointId": "DE:DCS:CHARGE_POINT:00ed1077-a85f-3539-a13e-11548ebe755a",
      "OperationalStateCP": "AVAILABLE",
      "Timestamp": "2025-12-24T22:08:51.000+00:00"
    }
  ],
  "ResponseStatus": {
    "code": 0,
    "description": "Success"
  }
}
```

**Possible States:** `AVAILABLE`, `OCCUPIED`, `OUT_OF_SERVICE`, `UNKNOWN`

---

### 6. Get Charging Prices ⭐ (THE MAIN PRICING ENDPOINT)

Get detailed pricing for a specific charge point and tariff.

```
POST /api/map/v1/{market}/tariffs/{tariffId}/prices
```

**Parameters:**
- `tariffId`: Tariff ID (e.g., `HYUNDAI_FLEX`)

**Request Body:**
```json
[
  {
    "charge_point": "DE:DCS:CHARGE_POINT:f82a935f-bd6c-3f69-aa6c-8abf9ce763af",
    "power_type": "AC",   // "AC" or "DC"
    "power": 11           // Power in kW
  }
]
```

**Example:**
```bash
curl -X POST "https://chargemyhyundai.com/api/map/v1/de/tariffs/HYUNDAI_FLEX/prices" \
  -H "Content-Type: application/json" \
  -d '[{"charge_point":"DE:DCS:CHARGE_POINT:f82a935f-bd6c-3f69-aa6c-8abf9ce763af","power_type":"AC","power":11}]'
```

**Response (AC Charging Example):**
```json
[
  {
    "id": "DE_VAT",
    "power_type": "AC_1_PHASE",
    "currency": "EUR",
    "elements": [
      {
        "price_components": [
          {"type": "ENERGY", "price": 0.48, "step_size": 1}
        ],
        "restrictions": {}
      },
      {
        "price_components": [
          {"type": "TIME", "price": 1.8, "step_size": 60}
        ],
        "restrictions": {"min_duration": 5400}  // Blocking fee after 90 min
      },
      {
        "price_components": [
          {"type": "FLAT", "price": 0.74, "step_size": 0}
        ],
        "restrictions": {}
      }
    ],
    "price_identifier": {
      "charge_point": "DE:DCS:CHARGE_POINT:f82a935f-bd6c-3f69-aa6c-8abf9ce763af",
      "power_type": "AC",
      "power": 11
    }
  }
]
```

**Response (DC Charging Example):**
```json
[
  {
    "id": "DE_VAT",
    "power_type": "DC",
    "currency": "EUR",
    "elements": [
      {
        "price_components": [
          {"type": "ENERGY", "price": 0.72, "step_size": 1}
        ],
        "restrictions": {}
      },
      {
        "price_components": [
          {"type": "FLAT", "price": 0.59, "step_size": 0}
        ],
        "restrictions": {}
      }
    ],
    "price_identifier": {
      "charge_point": "DE:DCS:CHARGE_POINT:42ba345b-a5b5-30c8-9cfd-1249a62c5337",
      "power_type": "DC",
      "power": 50
    }
  }
]
```

**Price Component Types:**
- `ENERGY`: Price per kWh
- `TIME`: Price per hour (often for blocking fees)
- `FLAT`: Session/transaction fee

**Restrictions:**
- `min_duration`: Time in seconds before this price applies (e.g., 5400 = 90 minutes for blocking fee)

---

## Workflow: Get Price for Any Charging Station

### Step 1: Find Charging Stations
Use the query endpoint with your desired geographic area to get pools and charge point IDs.

### Step 2: Get Price for Charge Point
Call the prices endpoint with a charge point ID, power type, and power level.

### Complete Example Script (JavaScript):

```javascript
const BASE_URL = 'https://chargemyhyundai.com/api/map/v1/de';

// Step 1: Find charging stations in Berlin area
async function findStations(lat, lng, radiusKm = 1) {
  const latOffset = radiusKm / 111;
  const lngOffset = radiusKm / (111 * Math.cos(lat * Math.PI / 180));
  
  const response = await fetch(`${BASE_URL}/query`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'rest-api-path': 'clusters'
    },
    body: JSON.stringify({
      searchCriteria: {
        latitudeNW: lat + latOffset,
        longitudeNW: lng - lngOffset,
        latitudeSE: lat - latOffset,
        longitudeSE: lng + lngOffset,
        precision: 10,
        unpackSolitudeCluster: true,
        unpackClustersWithSinglePool: true
      },
      withChargePointIds: true,
      filterCriteria: {
        authenticationMethods: [],
        cableAttachedTypes: [],
        paymentMethods: [],
        plugTypes: [],
        poolLocationTypes: [],
        valueAddedServices: [],
        dcsTcpoIds: []
      }
    })
  });
  
  return response.json();
}

// Step 2: Get pricing for a charge point
async function getPrice(chargePointId, powerType = 'AC', power = 11, tariffId = 'HYUNDAI_FLEX') {
  const response = await fetch(`${BASE_URL}/tariffs/${tariffId}/prices`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify([{
      charge_point: chargePointId,
      power_type: powerType,
      power: power
    }])
  });
  
  return response.json();
}

// Step 3: Get availability status
async function getStatus(chargePointIds) {
  const response = await fetch(`${BASE_URL}/query`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'rest-api-path': 'charge-points'
    },
    body: JSON.stringify({
      DCSChargePointDynStatusRequest: chargePointIds.map(id => ({ dcsChargePointId: id }))
    })
  });
  
  return response.json();
}

// Usage
async function main() {
  // Find stations near Berlin Alexanderplatz
  const stations = await findStations(52.5228, 13.4148);
  console.log(`Found ${stations.pools.length} charging pools`);
  
  if (stations.pools.length > 0) {
    const pool = stations.pools[0];
    const chargePointId = pool.chargePoints[0].id;
    
    // Get AC price
    const acPrice = await getPrice(chargePointId, 'AC', 11);
    console.log('AC Price:', acPrice);
    
    // Get status
    const status = await getStatus([chargePointId]);
    console.log('Status:', status);
  }
}

main();
```

---

## ID Formats

- **Pool ID**: `{country}:DCS:POOL:{uuid}`
  - Example: `DE:DCS:POOL:0f02b3e8-74e6-3ad2-9bdd-56fff6a52b9d`
  
- **Charge Point ID**: `{country}:DCS:CHARGE_POINT:{uuid}`
  - Example: `DE:DCS:CHARGE_POINT:00ed1077-a85f-3539-a13e-11548ebe755a`

- **Operator ID**: `{country}:DCS:TECH_CHARGE_POINT_OPERATOR:{uuid}`

---

## Available Tariffs

| Tariff ID | Name | Monthly Fee | Activation Fee |
|-----------|------|-------------|----------------|
| `HYUNDAI_FLEX` | Flex | 0.00 EUR | 7.49 EUR |

**Optional Packages:**
- `IONITY_D_V1` - IONITY Premium (6.99 EUR/month)
- `BPE_PULSE_P_V1` - Aral pulse premium (9.99 EUR/month)
- `BPE_PULSE_L_V1` - Aral pulse light (4.99 EUR/month)

---

## Rate Limits & Best Practices

1. **No authentication required** for public pricing (Flex tariff)
2. Prices are **CPO-specific** - same charger can have different prices depending on operator
3. Use **higher precision** (8-10) when querying to get individual stations
4. **Cache tariff data** - it changes infrequently
5. **Status updates** are real-time - poll responsibly

---

## Notes

- All prices include VAT (MwSt.)
- Blocking fees typically apply after 90 minutes of charging
- The API follows OCPI (Open Charge Point Interface) conventions
- Response timestamps are in UTC

---

*Last updated: December 2025*
*Discovered by reverse engineering the ChargeMyHyundai web application*
