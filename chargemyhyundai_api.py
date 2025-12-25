"""
ChargeMyHyundai API Client

A Python client for accessing charging station information and pricing
from the ChargeMyHyundai network.

Usage:
    python chargemyhyundai_api.py

Requirements:
    pip install requests
"""

import requests
from dataclasses import dataclass
from typing import Optional


@dataclass
class ChargingPrice:
    """Represents pricing for a charge point"""
    energy_price_per_kwh: float
    session_fee: float
    blocking_fee_per_hour: Optional[float] = None
    blocking_fee_starts_after_minutes: Optional[int] = None
    currency: str = "EUR"
    power_type: str = "AC"
    
    def __str__(self):
        result = f"Energy: {self.energy_price_per_kwh:.2f} {self.currency}/kWh"
        result += f" | Session Fee: {self.session_fee:.2f} {self.currency}"
        if self.blocking_fee_per_hour:
            result += f" | Blocking: {self.blocking_fee_per_hour:.2f} {self.currency}/h after {self.blocking_fee_starts_after_minutes}min"
        return result


class ChargeMyHyundaiAPI:
    """Client for the ChargeMyHyundai API"""
    
    def __init__(self, market: str = "de", locale: str = "de_DE"):
        self.base_url = "https://chargemyhyundai.com/api/map/v1"
        self.market = market
        self.locale = locale
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://chargemyhyundai.com",
            "Referer": "https://chargemyhyundai.com/web/de/hyundai-de/map"
        })
    
    @property
    def market_url(self) -> str:
        return f"{self.base_url}/{self.market}"
    
    def get_init(self) -> dict:
        """Get map configuration and feature flags"""
        response = self.session.get(
            f"{self.market_url}/init",
            params={"locale": self.locale}
        )
        response.raise_for_status()
        return response.json()
    
    def get_markets(self) -> list:
        """Get all available markets with charge point counts"""
        response = self.session.get(
            f"{self.market_url}/markets",
            params={"locale": self.locale}
        )
        response.raise_for_status()
        return response.json()
    
    def get_tariffs(self) -> list:
        """Get available tariff plans"""
        response = self.session.get(
            f"{self.market_url}/tariffs",
            params={"locale": self.locale}
        )
        response.raise_for_status()
        return response.json()
    
    def find_stations(
        self,
        lat: float,
        lng: float,
        radius_km: float = 1.0,
        precision: int = 10
    ) -> dict:
        """
        Find charging stations near a location.
        
        Args:
            lat: Latitude of center point
            lng: Longitude of center point
            radius_km: Search radius in kilometers
            precision: Query precision (6-10, higher = more detail)
        
        Returns:
            Dict with 'pools' and 'poolClusters' keys
        """
        # Calculate bounding box
        lat_offset = radius_km / 111
        lng_offset = radius_km / (111 * abs(lat) * 0.0175)  # Rough approximation
        
        payload = {
            "searchCriteria": {
                "latitudeNW": lat + lat_offset,
                "longitudeNW": lng - lng_offset,
                "latitudeSE": lat - lat_offset,
                "longitudeSE": lng + lng_offset,
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
        
        response = self.session.post(
            f"{self.market_url}/query",
            json=payload,
            headers={"rest-api-path": "clusters"}
        )
        response.raise_for_status()
        return response.json()
    
    def get_charge_point_status(self, charge_point_ids: list) -> dict:
        """
        Get real-time availability of charge points.
        
        Args:
            charge_point_ids: List of charge point IDs
        
        Returns:
            Status response with availability information
        """
        payload = {
            "DCSChargePointDynStatusRequest": [
                {"dcsChargePointId": cp_id} for cp_id in charge_point_ids
            ]
        }
        
        response = self.session.post(
            f"{self.market_url}/query",
            json=payload,
            headers={"rest-api-path": "charge-points"}
        )
        response.raise_for_status()
        return response.json()
    
    def get_price(
        self,
        charge_point_id: str,
        power_type: str = "AC",
        power_kw: int = 11,
        tariff_id: str = "HYUNDAI_FLEX"
    ) -> ChargingPrice:
        """
        Get pricing for a specific charge point.
        
        Args:
            charge_point_id: The charge point ID (e.g., DE:DCS:CHARGE_POINT:...)
            power_type: "AC" or "DC"
            power_kw: Power level in kW
            tariff_id: Tariff ID (default: HYUNDAI_FLEX)
        
        Returns:
            ChargingPrice object with pricing details
        """
        payload = [{
            "charge_point": charge_point_id,
            "power_type": power_type,
            "power": power_kw
        }]
        
        response = self.session.post(
            f"{self.market_url}/tariffs/{tariff_id}/prices",
            json=payload
        )
        response.raise_for_status()
        
        data = response.json()[0]
        
        # Parse price components
        energy_price = 0.0
        session_fee = 0.0
        blocking_fee = None
        blocking_after_min = None
        
        for element in data.get("elements", []):
            for component in element.get("price_components", []):
                comp_type = component.get("type")
                price = component.get("price", 0)
                
                if comp_type == "ENERGY":
                    energy_price = price
                elif comp_type == "FLAT":
                    session_fee = price
                elif comp_type == "TIME":
                    blocking_fee = price
                    min_duration = element.get("restrictions", {}).get("min_duration")
                    if min_duration:
                        blocking_after_min = min_duration // 60
        
        return ChargingPrice(
            energy_price_per_kwh=energy_price,
            session_fee=session_fee,
            blocking_fee_per_hour=blocking_fee,
            blocking_fee_starts_after_minutes=blocking_after_min,
            currency=data.get("currency", "EUR"),
            power_type=data.get("power_type", power_type)
        )
    
    def get_price_raw(
        self,
        charge_point_id: str,
        power_type: str = "AC",
        power_kw: int = 11,
        tariff_id: str = "HYUNDAI_FLEX"
    ) -> dict:
        """Get raw price response (for debugging/advanced use)"""
        payload = [{
            "charge_point": charge_point_id,
            "power_type": power_type,
            "power": power_kw
        }]
        
        response = self.session.post(
            f"{self.market_url}/tariffs/{tariff_id}/prices",
            json=payload
        )
        response.raise_for_status()
        return response.json()


def main():
    """Example usage of the ChargeMyHyundai API"""
    
    api = ChargeMyHyundaiAPI()
    
    print("=" * 60)
    print("ChargeMyHyundai API Demo")
    print("=" * 60)
    
    # Get available markets
    print("\n📊 Available Markets:")
    markets = api.get_markets()
    for market in markets[:5]:
        print(f"  {market['countryCode']}: {market['numberOfChargePoints']:,} charge points")
    print(f"  ... and {len(markets) - 5} more countries")
    
    # Find stations near Berlin Alexanderplatz
    print("\n🔍 Finding charging stations near Berlin Alexanderplatz...")
    stations = api.find_stations(lat=52.5228, lng=13.4148, radius_km=0.5)
    
    pools = stations.get("pools", [])
    print(f"  Found {len(pools)} charging pools")
    
    if pools:
        # Get first pool details
        pool = pools[0]
        print(f"\n📍 First Pool: {pool['id']}")
        print(f"   Location: {pool['latitude']:.6f}, {pool['longitude']:.6f}")
        print(f"   Charge Points: {pool['chargePointCount']}")
        
        # Get status for first charge point
        if pool.get("chargePoints"):
            cp_id = pool["chargePoints"][0]["id"]
            print(f"\n⚡ Charge Point: {cp_id}")
            
            # Get status
            status = api.get_charge_point_status([cp_id])
            for resp in status.get("DCSChargePointDynStatusResponse", []):
                print(f"   Status: {resp['OperationalStateCP']}")
                print(f"   Last Update: {resp['Timestamp']}")
            
            # Get AC price
            print("\n💰 AC Charging Price (Flex Tariff):")
            try:
                ac_price = api.get_price(cp_id, power_type="AC", power_kw=11)
                print(f"   {ac_price}")
            except Exception as e:
                print(f"   Error: {e}")
            
            # Get DC price if available
            print("\n💰 DC Charging Price (Flex Tariff):")
            try:
                dc_price = api.get_price(cp_id, power_type="DC", power_kw=50)
                print(f"   {dc_price}")
            except Exception as e:
                print(f"   Error: {e}")
    
    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
