"""
Station Cache Module - SQLite-based caching for ChargeMyHyundai stations

This module provides persistent caching for:
- Station details (pool info, addresses, CPO names)
- Prices (AC/DC prices by tariff)
- Background update scheduling with rate limiting
"""

import sqlite3
import json
import threading
import time
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager

# Database file location (configurable via environment variable for Docker)
DB_PATH = os.environ.get('CACHE_DB_PATH', os.path.join(os.path.dirname(__file__), 'station_cache.db'))

# Cache settings
CACHE_EXPIRY_HOURS = 24  # Cached data is considered stale after 24 hours
RATE_LIMIT_REQUESTS = 3  # Max requests per rate limit window
RATE_LIMIT_WINDOW_SECONDS = 10  # Rate limit window


class StationCache:
    """SQLite-based cache for charging station data"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()
        
        # Rate limiting state
        self._rate_limit_lock = threading.Lock()
        self._request_times: List[float] = []
        
        # Background update state
        self._update_thread: Optional[threading.Thread] = None
        self._update_running = False
        self._update_stop_event = threading.Event()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    @contextmanager
    def _cursor(self):
        """Context manager for database cursor with commit"""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    def _init_db(self):
        """Initialize database schema"""
        with self._cursor() as cursor:
            # Stations table - stores pool details
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stations (
                    pool_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    cpo_id TEXT,
                    cpo_name TEXT,
                    location_name TEXT,
                    street TEXT,
                    city TEXT,
                    zip_code TEXT,
                    latitude REAL,
                    longitude REAL,
                    max_power INTEGER,
                    plug_types TEXT,  -- JSON array
                    charge_points_ac TEXT,  -- JSON array of charge point IDs
                    charge_points_dc TEXT,  -- JSON array of charge point IDs
                    contact_name TEXT,
                    contact_phone TEXT,
                    charge_point_count INTEGER,
                    raw_data TEXT,  -- Full API response for reference
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Prices table - stores price data by station, tariff, and power type
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_id TEXT NOT NULL,
                    charge_point_id TEXT NOT NULL,
                    tariff_id TEXT NOT NULL,
                    power_type TEXT NOT NULL,  -- AC or DC
                    power INTEGER NOT NULL,  -- kW
                    market TEXT NOT NULL,
                    currency TEXT DEFAULT 'EUR',
                    energy_price REAL,
                    session_fee REAL,
                    blocking_fee REAL,
                    blocking_after_minutes INTEGER,
                    raw_data TEXT,  -- Full price response
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(pool_id, tariff_id, power_type, market)
                )
            ''')
            
            # Update queue - tracks stations needing updates
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS update_queue (
                    pool_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,  -- Higher = update sooner
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_attempt TIMESTAMP,
                    attempt_count INTEGER DEFAULT 0
                )
            ''')
            
            # Update log - tracks update history
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS update_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_id TEXT,
                    update_type TEXT,  -- 'station', 'price', 'full'
                    success INTEGER,
                    error_message TEXT,
                    duration_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for faster queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_stations_market ON stations(market)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_stations_updated ON stations(updated_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_prices_pool ON prices(pool_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_prices_updated ON prices(updated_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_queue_priority ON update_queue(priority DESC, added_at ASC)')
    
    # ==================== Station Methods ====================
    
    def get_station(self, pool_id: str) -> Optional[Dict[str, Any]]:
        """Get cached station data by pool ID"""
        with self._cursor() as cursor:
            cursor.execute('SELECT * FROM stations WHERE pool_id = ?', (pool_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_station(row)
        return None
    
    def get_stations(self, pool_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get multiple cached stations by pool IDs"""
        if not pool_ids:
            return {}
        
        result = {}
        with self._cursor() as cursor:
            placeholders = ','.join('?' * len(pool_ids))
            cursor.execute(
                f'SELECT * FROM stations WHERE pool_id IN ({placeholders})',
                pool_ids
            )
            for row in cursor.fetchall():
                station = self._row_to_station(row)
                result[station['pool_id']] = station
        return result
    
    def get_stations_in_bounds(self, lat_nw: float, lng_nw: float, 
                               lat_se: float, lng_se: float,
                               market: str = None) -> List[Dict[str, Any]]:
        """
        Get all cached stations within a geographic bounding box.
        Returns stations with coordinates for fast initial map display.
        """
        result = []
        with self._cursor() as cursor:
            if market:
                cursor.execute('''
                    SELECT * FROM stations 
                    WHERE latitude IS NOT NULL 
                    AND longitude IS NOT NULL
                    AND latitude <= ? AND latitude >= ?
                    AND longitude >= ? AND longitude <= ?
                    AND market = ?
                ''', (lat_nw, lat_se, lng_nw, lng_se, market))
            else:
                cursor.execute('''
                    SELECT * FROM stations 
                    WHERE latitude IS NOT NULL 
                    AND longitude IS NOT NULL
                    AND latitude <= ? AND latitude >= ?
                    AND longitude >= ? AND longitude <= ?
                ''', (lat_nw, lat_se, lng_nw, lng_se))
            
            for row in cursor.fetchall():
                result.append(self._row_to_station(row))
        return result
    
    def get_all_stations(self, market: str = None) -> List[Dict[str, Any]]:
        """
        Get all cached stations (for initial load).
        Returns stations with coordinates.
        """
        result = []
        with self._cursor() as cursor:
            if market:
                cursor.execute('''
                    SELECT * FROM stations 
                    WHERE latitude IS NOT NULL 
                    AND longitude IS NOT NULL
                    AND market = ?
                ''', (market,))
            else:
                cursor.execute('''
                    SELECT * FROM stations 
                    WHERE latitude IS NOT NULL 
                    AND longitude IS NOT NULL
                ''')
            
            for row in cursor.fetchall():
                result.append(self._row_to_station(row))
        return result
    
    def save_station(self, pool_id: str, market: str, data: Dict[str, Any], 
                     latitude: float = None, longitude: float = None,
                     charge_point_count: int = None, cpo_id: str = None):
        """Save or update station data in cache"""
        with self._cursor() as cursor:
            now = datetime.utcnow().isoformat()
            
            cursor.execute('''
                INSERT INTO stations (
                    pool_id, market, cpo_id, cpo_name, location_name, street, city, 
                    zip_code, latitude, longitude, max_power, plug_types,
                    charge_points_ac, charge_points_dc, contact_name, contact_phone,
                    charge_point_count, raw_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_id) DO UPDATE SET
                    cpo_name = excluded.cpo_name,
                    location_name = excluded.location_name,
                    street = excluded.street,
                    city = excluded.city,
                    zip_code = excluded.zip_code,
                    latitude = COALESCE(excluded.latitude, latitude),
                    longitude = COALESCE(excluded.longitude, longitude),
                    max_power = excluded.max_power,
                    plug_types = excluded.plug_types,
                    charge_points_ac = excluded.charge_points_ac,
                    charge_points_dc = excluded.charge_points_dc,
                    contact_name = excluded.contact_name,
                    contact_phone = excluded.contact_phone,
                    charge_point_count = COALESCE(excluded.charge_point_count, charge_point_count),
                    raw_data = excluded.raw_data,
                    updated_at = excluded.updated_at
            ''', (
                pool_id,
                market,
                cpo_id,
                data.get('cpo_name'),
                data.get('location_name'),
                data.get('street'),
                data.get('city'),
                data.get('zip_code'),
                latitude,
                longitude,
                data.get('max_power'),
                json.dumps(data.get('plug_types', [])),
                json.dumps(data.get('charge_points_ac', [])),
                json.dumps(data.get('charge_points_dc', [])),
                data.get('contact_name'),
                data.get('contact_phone'),
                charge_point_count,
                json.dumps(data),
                now,
                now
            ))
    
    def _row_to_station(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert database row to station dict"""
        return {
            'pool_id': row['pool_id'],
            'market': row['market'],
            'cpo_id': row['cpo_id'],
            'cpo_name': row['cpo_name'],
            'location_name': row['location_name'],
            'street': row['street'],
            'city': row['city'],
            'zip_code': row['zip_code'],
            'latitude': row['latitude'],
            'longitude': row['longitude'],
            'max_power': row['max_power'],
            'plug_types': json.loads(row['plug_types']) if row['plug_types'] else [],
            'charge_points_ac': json.loads(row['charge_points_ac']) if row['charge_points_ac'] else [],
            'charge_points_dc': json.loads(row['charge_points_dc']) if row['charge_points_dc'] else [],
            'contact_name': row['contact_name'],
            'contact_phone': row['contact_phone'],
            'charge_point_count': row['charge_point_count'],
            'updated_at': row['updated_at'],
            'cached': True
        }
    
    # ==================== Price Methods ====================
    
    def get_price(self, pool_id: str, tariff_id: str, power_type: str, 
                  market: str) -> Optional[Dict[str, Any]]:
        """Get cached price for a station"""
        with self._cursor() as cursor:
            cursor.execute('''
                SELECT * FROM prices 
                WHERE pool_id = ? AND tariff_id = ? AND power_type = ? AND market = ?
            ''', (pool_id, tariff_id, power_type, market))
            row = cursor.fetchone()
            if row:
                return self._row_to_price(row)
        return None
    
    def get_prices(self, pool_ids: List[str], tariff_id: str, power_type: str,
                   market: str) -> Dict[str, Dict[str, Any]]:
        """Get cached prices for multiple stations"""
        if not pool_ids:
            return {}
        
        result = {}
        with self._cursor() as cursor:
            placeholders = ','.join('?' * len(pool_ids))
            cursor.execute(f'''
                SELECT * FROM prices 
                WHERE pool_id IN ({placeholders}) 
                AND tariff_id = ? AND power_type = ? AND market = ?
            ''', (*pool_ids, tariff_id, power_type, market))
            for row in cursor.fetchall():
                price = self._row_to_price(row)
                result[price['pool_id']] = price
        return result
    
    def get_all_prices_for_pools(self, pool_ids: List[str], market: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """
        Get all cached prices for multiple stations (all tariffs, all power types).
        Returns: {pool_id: {tariff_powertype: price_data}}
        e.g. {'pool123': {'HYUNDAI_SMART_AC': {...}, 'HYUNDAI_SMART_DC': {...}, 'HYUNDAI_FLEX_AC': {...}}}
        """
        if not pool_ids:
            return {}
        
        result = {}
        with self._cursor() as cursor:
            placeholders = ','.join('?' * len(pool_ids))
            cursor.execute(f'''
                SELECT * FROM prices 
                WHERE pool_id IN ({placeholders}) AND market = ?
            ''', (*pool_ids, market))
            for row in cursor.fetchall():
                price = self._row_to_price(row)
                pool_id = price['pool_id']
                key = f"{price['tariff_id']}_{price['power_type']}"
                
                if pool_id not in result:
                    result[pool_id] = {}
                result[pool_id][key] = price
        return result
    
    def save_price(self, pool_id: str, charge_point_id: str, tariff_id: str,
                   power_type: str, power: int, market: str, data: Dict[str, Any]):
        """Save or update price data in cache"""
        with self._cursor() as cursor:
            now = datetime.utcnow().isoformat()
            
            cursor.execute('''
                INSERT INTO prices (
                    pool_id, charge_point_id, tariff_id, power_type, power, market,
                    currency, energy_price, session_fee, blocking_fee, 
                    blocking_after_minutes, raw_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_id, tariff_id, power_type, market) DO UPDATE SET
                    charge_point_id = excluded.charge_point_id,
                    power = excluded.power,
                    currency = excluded.currency,
                    energy_price = excluded.energy_price,
                    session_fee = excluded.session_fee,
                    blocking_fee = excluded.blocking_fee,
                    blocking_after_minutes = excluded.blocking_after_minutes,
                    raw_data = excluded.raw_data,
                    updated_at = excluded.updated_at
            ''', (
                pool_id,
                charge_point_id,
                tariff_id,
                power_type,
                power,
                market,
                data.get('currency', 'EUR'),
                data.get('energy_price'),
                data.get('session_fee'),
                data.get('blocking_fee'),
                data.get('blocking_after_minutes'),
                json.dumps(data),
                now,
                now
            ))
    
    def _row_to_price(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert database row to price dict"""
        return {
            'pool_id': row['pool_id'],
            'charge_point': row['charge_point_id'],
            'tariff_id': row['tariff_id'],
            'power_type': row['power_type'],
            'power': row['power'],
            'market': row['market'],
            'currency': row['currency'],
            'energy_price': row['energy_price'],
            'session_fee': row['session_fee'],
            'blocking_fee': row['blocking_fee'],
            'blocking_after_minutes': row['blocking_after_minutes'],
            'updated_at': row['updated_at'],
            'cached': True
        }
    
    # ==================== Cache Freshness ====================
    
    def is_station_stale(self, pool_id: str, max_age_hours: int = CACHE_EXPIRY_HOURS) -> bool:
        """Check if station data needs updating"""
        station = self.get_station(pool_id)
        if not station:
            return True
        
        updated_at = datetime.fromisoformat(station['updated_at'])
        age = datetime.utcnow() - updated_at
        return age > timedelta(hours=max_age_hours)
    
    def get_stale_stations(self, market: str = None, limit: int = 100) -> List[str]:
        """Get list of station IDs that need updating"""
        with self._cursor() as cursor:
            cutoff = (datetime.utcnow() - timedelta(hours=CACHE_EXPIRY_HOURS)).isoformat()
            
            if market:
                cursor.execute('''
                    SELECT pool_id FROM stations 
                    WHERE market = ? AND updated_at < ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                ''', (market, cutoff, limit))
            else:
                cursor.execute('''
                    SELECT pool_id FROM stations 
                    WHERE updated_at < ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                ''', (cutoff, limit))
            
            return [row['pool_id'] for row in cursor.fetchall()]
    
    # ==================== Update Queue ====================
    
    def queue_update(self, pool_id: str, market: str, priority: int = 0):
        """Add a station to the update queue"""
        with self._cursor() as cursor:
            now = datetime.utcnow().isoformat()
            cursor.execute('''
                INSERT INTO update_queue (pool_id, market, priority, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pool_id) DO UPDATE SET
                    priority = MAX(excluded.priority, update_queue.priority),
                    added_at = CASE 
                        WHEN excluded.priority > update_queue.priority THEN excluded.added_at
                        ELSE update_queue.added_at
                    END
            ''', (pool_id, market, priority, now))
    
    def get_next_update(self) -> Optional[Tuple[str, str]]:
        """Get next station to update from queue"""
        with self._cursor() as cursor:
            cursor.execute('''
                SELECT pool_id, market FROM update_queue
                ORDER BY priority DESC, added_at ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
                return (row['pool_id'], row['market'])
        return None
    
    def remove_from_queue(self, pool_id: str):
        """Remove station from update queue"""
        with self._cursor() as cursor:
            cursor.execute('DELETE FROM update_queue WHERE pool_id = ?', (pool_id,))
    
    def get_queue_size(self) -> int:
        """Get number of stations in update queue"""
        with self._cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM update_queue')
            return cursor.fetchone()[0]
    
    # ==================== Rate Limiting ====================
    
    def can_make_request(self) -> bool:
        """Check if we can make an API request within rate limits"""
        with self._rate_limit_lock:
            now = time.time()
            # Remove old request times outside the window
            self._request_times = [
                t for t in self._request_times 
                if now - t < RATE_LIMIT_WINDOW_SECONDS
            ]
            return len(self._request_times) < RATE_LIMIT_REQUESTS
    
    def record_request(self):
        """Record that an API request was made"""
        with self._rate_limit_lock:
            self._request_times.append(time.time())
    
    def wait_for_rate_limit(self) -> float:
        """Wait until we can make a request, returns wait time"""
        while not self.can_make_request():
            time.sleep(0.5)
        return 0
    
    # ==================== Statistics ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM stations')
            total_stations = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM prices')
            total_prices = cursor.fetchone()[0]
            
            cutoff = (datetime.utcnow() - timedelta(hours=CACHE_EXPIRY_HOURS)).isoformat()
            cursor.execute('SELECT COUNT(*) FROM stations WHERE updated_at >= ?', (cutoff,))
            fresh_stations = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM update_queue')
            queue_size = cursor.fetchone()[0]
            
            return {
                'total_stations': total_stations,
                'total_prices': total_prices,
                'fresh_stations': fresh_stations,
                'stale_stations': total_stations - fresh_stations,
                'queue_size': queue_size,
                'cache_expiry_hours': CACHE_EXPIRY_HOURS
            }
    
    def log_update(self, pool_id: str, update_type: str, success: bool,
                   error_message: str = None, duration_ms: int = None):
        """Log an update attempt"""
        with self._cursor() as cursor:
            cursor.execute('''
                INSERT INTO update_log (pool_id, update_type, success, error_message, duration_ms)
                VALUES (?, ?, ?, ?, ?)
            ''', (pool_id, update_type, 1 if success else 0, error_message, duration_ms))
    
    # ==================== Cleanup ====================
    
    def cleanup_old_logs(self, days: int = 7):
        """Remove old update logs"""
        with self._cursor() as cursor:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            cursor.execute('DELETE FROM update_log WHERE created_at < ?', (cutoff,))
    
    def vacuum(self):
        """Optimize database file"""
        conn = self._get_conn()
        conn.execute('VACUUM')


# Global cache instance
_cache_instance: Optional[StationCache] = None


def get_cache() -> StationCache:
    """Get the global cache instance"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = StationCache()
    return _cache_instance
