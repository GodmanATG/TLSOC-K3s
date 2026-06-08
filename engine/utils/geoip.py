import geoip2.database
import ipaddress
import os
import yaml

class GeoIPClient:
    _instance = None
    _reader = None

    def __new__(cls, config_path="config.yaml"):
        if cls._instance is None:
            cls._instance = super(GeoIPClient, cls).__new__(cls)
            cls._instance._initialize(config_path)
        return cls._instance

    def _initialize(self, config_file):
        try:
            # Resolve config path relative to the project root
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, config_file)
            
            with open(config_path, 'r') as f:
                conf = yaml.safe_load(f)
            
            db_rel_path = conf.get('geoip', {}).get('db_path')
            
            if db_rel_path:
                db_abs_path = os.path.join(base_dir, db_rel_path)
                if os.path.exists(db_abs_path):
                    self._reader = geoip2.database.Reader(db_abs_path)
                    print(f"GeoIP Database loaded: {db_abs_path}")
                else:
                    print(f"GeoIP Database file not found: {db_abs_path}")
                    self._reader = None
            else:
                self._reader = None

        except Exception as e:
            print(f"GeoIP initialization error: {e}")
            self._reader = None

    def enrich(self, ip_str):
        if not self._reader or not ip_str:
            return None

        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback:
                return None

            response = self._reader.city(ip_str)
            
            return {
                "country_name": response.country.name,
                "country_iso_code": response.country.iso_code,
                "city_name": response.city.name,
                "location": {
                    "lat": response.location.latitude,
                    "lon": response.location.longitude
                }
            }
        except Exception:
            return None
