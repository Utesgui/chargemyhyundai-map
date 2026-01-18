"""
Background Updater Service for Station Cache

This module provides automatic background updates for cached charging station data.
It respects rate limits and runs continuously to keep the cache fresh.
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional, Dict, Any, List
import requests

from station_cache import get_cache, CACHE_EXPIRY_HOURS

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BackgroundUpdater:
    """Background service for updating cached station data"""
    
    def __init__(self, 
                 session: requests.Session,
                 base_url: str = "https://chargemyhyundai.com/api/map/v1",
                 default_market: str = "de",
                 default_tariffs: List[str] = None):
        """
        Initialize the background updater.
        
        Args:
            session: Configured requests session with proper headers (used as template)
            base_url: ChargeMyHyundai API base URL
            default_market: Default market code
            default_tariffs: List of tariff IDs to update prices for
        """
        self.cache = get_cache()
        self.base_url = base_url
        self.default_market = default_market
        self.default_tariffs = default_tariffs or ['HYUNDAI_FLEX', 'HYUNDAI_SMART']
        
        # Threading control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        
        # Update tracking
        self._last_update_time: Optional[datetime] = None
        self._updates_today = 0
        self._errors_today = 0
    
    def _create_session(self) -> requests.Session:
        """Create a fresh session with proper headers for API calls"""
        s = requests.Session()
        s.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Origin': 'https://chargemyhyundai.com',
            'Referer': 'https://chargemyhyundai.com/web/de/hyundai-de/map'
        })
        return s
    
    def start(self):
        """Start the background updater thread"""
        if self._running:
            logger.warning("Background updater is already running")
            return
        
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.name = "StationCacheUpdater"
        self._thread.start()
        logger.info("Background updater started")
    
    def stop(self):
        """Stop the background updater thread"""
        if not self._running:
            return
        
        logger.info("Stopping background updater...")
        self._stop_event.set()
        self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        
        logger.info("Background updater stopped")
    
    def is_running(self) -> bool:
        """Check if the updater is running"""
        return self._running and self._thread and self._thread.is_alive()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current updater status"""
        cache_stats = self.cache.get_stats()
        return {
            'running': self.is_running(),
            'last_update': self._last_update_time.isoformat() if self._last_update_time else None,
            'updates_today': self._updates_today,
            'errors_today': self._errors_today,
            'queue_size': cache_stats['queue_size'],
            'stale_stations': cache_stats['stale_stations'],
            'total_cached_stations': cache_stats['total_stations'],
            'fresh_stations': cache_stats['fresh_stations']
        }
    
    def _update_loop(self):
        """Main update loop - runs continuously in background"""
        logger.info("Update loop started")
        
        # Wait a bit before starting to let the app initialize
        time.sleep(5)
        
        while not self._stop_event.is_set():
            try:
                # First, queue any stale stations that need updating
                self._queue_stale_stations()
                
                # Process update queue
                updated = self._process_queue_item()
                
                if not updated:
                    # No items to update, wait before checking again
                    self._stop_event.wait(timeout=60)  # Wait 1 minute
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}", exc_info=True)
                self._errors_today += 1
                # Wait a bit before retrying after error
                self._stop_event.wait(timeout=30)
        
        logger.info("Update loop ended")
    
    def _queue_stale_stations(self):
        """Add stale stations to the update queue"""
        stale_ids = self.cache.get_stale_stations(limit=50)
        
        for pool_id in stale_ids:
            station = self.cache.get_station(pool_id)
            if station:
                market = station.get('market', self.default_market)
                # Lower priority for automatic background updates
                self.cache.queue_update(pool_id, market, priority=1)
    
    def _process_queue_item(self) -> bool:
        """
        Process one item from the update queue.
        Returns True if an item was processed, False if queue is empty.
        """
        # Check rate limit
        self.cache.wait_for_rate_limit()
        
        # Get next item from queue
        item = self.cache.get_next_update()
        if not item:
            return False
        
        pool_id, market = item
        start_time = time.time()
        success = False
        error_msg = None
        
        try:
            # Fetch fresh data from API
            self._update_station(pool_id, market)
            
            # Record the request for rate limiting
            self.cache.record_request()
            
            success = True
            self._updates_today += 1
            self._last_update_time = datetime.utcnow()
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Failed to update station {pool_id}: {e}")
            self._errors_today += 1
        
        finally:
            # Remove from queue regardless of success
            self.cache.remove_from_queue(pool_id)
            
            # Log the update attempt
            duration_ms = int((time.time() - start_time) * 1000)
            self.cache.log_update(pool_id, 'full', success, error_msg, duration_ms)
        
        return True
    
    def _update_station(self, pool_id: str, market: str):
        """Fetch and update a single station's data"""
        # Fetch pool details
        pool_data = self._fetch_pool_details(pool_id, market)
        
        if pool_data:
            # Save station data
            self.cache.save_station(pool_id, market, pool_data)
            
            # Get charge point IDs for price fetching
            ac_cps = pool_data.get('charge_points_ac', [])
            dc_cps = pool_data.get('charge_points_dc', [])
            
            # Fetch prices for each tariff
            for tariff_id in self.default_tariffs:
                # Wait for rate limit before each price request
                self.cache.wait_for_rate_limit()
                
                # AC prices
                if ac_cps:
                    self._fetch_and_save_price(
                        pool_id, ac_cps[0], tariff_id, 'AC', 11, market
                    )
                    self.cache.record_request()
                
                # Wait between AC and DC requests
                self.cache.wait_for_rate_limit()
                
                # DC prices  
                if dc_cps:
                    self._fetch_and_save_price(
                        pool_id, dc_cps[0], tariff_id, 'DC', 50, market
                    )
                    self.cache.record_request()
    
    def _fetch_pool_details(self, pool_id: str, market: str) -> Optional[Dict[str, Any]]:
        """Fetch pool details from API"""
        try:
            session = self._create_session()
            response = session.post(
                f"{self.base_url}/{market}/query",
                json={"dcsPoolIds": [pool_id]},
                headers={"rest-api-path": "pools"},
                timeout=30
            )
            
            if not response.ok:
                logger.warning(f"Pool details API returned {response.status_code}")
                return None
            
            pools_data = response.json()
            if not pools_data:
                return None
            
            pool = pools_data[0]
            
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
                        
                        # Classify by connector type
                        plug_upper = plug_type.upper()
                        if 'TYP2' in plug_upper or 'TYPE2' in plug_upper:
                            cp_type = 'AC'
                        elif 'CCS' in plug_upper or 'COMBO' in plug_upper:
                            cp_type = 'DC'
                        else:
                            cp_type = connector.get('phaseType', 'AC')
                        
                        if cp_id not in charge_points_by_type[cp_type]:
                            charge_points_by_type[cp_type].append(cp_id)
            
            return {
                'pool_id': pool_id,
                'cpo_name': cpo_name,
                'location_name': location_name,
                'street': street,
                'city': city,
                'zip_code': zip_code,
                'max_power': max_power,
                'plug_types': list(plug_types),
                'charge_points_ac': charge_points_by_type.get('AC', []),
                'charge_points_dc': charge_points_by_type.get('DC', [])
            }
            
        except Exception as e:
            logger.error(f"Error fetching pool details for {pool_id}: {e}")
            return None
    
    def _fetch_and_save_price(self, pool_id: str, charge_point_id: str,
                              tariff_id: str, power_type: str, power: int,
                              market: str):
        """Fetch and save price data for a charge point"""
        try:
            payload = [{
                "charge_point": charge_point_id,
                "power_type": power_type,
                "power": power
            }]
            
            session = self._create_session()
            response = session.post(
                f"{self.base_url}/{market}/tariffs/{tariff_id}/prices",
                json=payload,
                timeout=30
            )
            
            if not response.ok:
                logger.warning(f"Price API returned {response.status_code}")
                return
            
            response_data = response.json()
            if not response_data:
                return
            
            item = response_data[0]
            
            # Parse price components
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
            
            price_data = {
                'charge_point': charge_point_id,
                'power_type': power_type,
                'power': power,
                'currency': currency,
                'energy_price': energy_price,
                'session_fee': session_fee,
                'blocking_fee': blocking_fee,
                'blocking_after_minutes': blocking_after
            }
            
            self.cache.save_price(
                pool_id, charge_point_id, tariff_id,
                power_type, power, market, price_data
            )
            
        except Exception as e:
            logger.error(f"Error fetching price for {charge_point_id}: {e}")
    
    def force_update(self, pool_id: str, market: str):
        """
        Immediately update a specific station (for manual refresh button).
        This bypasses the queue and updates synchronously.
        """
        start_time = time.time()
        success = False
        error_msg = None
        
        try:
            # Check rate limit
            if not self.cache.can_make_request():
                raise Exception("Rate limit exceeded, please wait a moment")
            
            # Fetch fresh data
            self._update_station(pool_id, market)
            self.cache.record_request()
            
            success = True
            self._updates_today += 1
            self._last_update_time = datetime.utcnow()
            
        except Exception as e:
            error_msg = str(e)
            raise
        
        finally:
            # Remove from queue if it was queued
            self.cache.remove_from_queue(pool_id)
            
            # Log the update
            duration_ms = int((time.time() - start_time) * 1000)
            self.cache.log_update(pool_id, 'manual', success, error_msg, duration_ms)
        
        return self.cache.get_station(pool_id)


# Global updater instance
_updater_instance: Optional[BackgroundUpdater] = None


def get_updater() -> Optional[BackgroundUpdater]:
    """Get the global updater instance"""
    return _updater_instance


def init_updater(session: requests.Session, **kwargs) -> BackgroundUpdater:
    """Initialize and return the global updater instance"""
    global _updater_instance
    if _updater_instance is None:
        _updater_instance = BackgroundUpdater(session, **kwargs)
    return _updater_instance
