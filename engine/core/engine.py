import re
import os
import json
import redis
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from utils.geoip import GeoIPClient

# Initialize Redis connection
# CHANGED FOR CONTAINERISATION:
# In Docker/Kubernetes, Redis runs as a separate container and is reachable
# by its service name (e.g. 'redis'), NOT 'localhost'.
# os.environ.get('REDIS_HOST', 'localhost') means:
#   - If REDIS_HOST environment variable is set → use it (e.g. 'redis' in Docker)
#   - If not set → fall back to 'localhost' (for bare-metal / dev use)
# The docker-compose.yml sets REDIS_HOST=redis so containers find each other.
r = redis.Redis(
    host=os.environ.get('REDIS_HOST', 'localhost'),
    port=int(os.environ.get('REDIS_PORT', 6379)),
    db=0,
    decode_responses=True
)
logger = logging.getLogger("soc-engine")

class UniversalEngine:
    def __init__(self, rule_config):
        self.config = rule_config
        self.strategy = rule_config.get('strategy', 'stateless')
        self.geoip = GeoIPClient()
        self.disabled = False
        self.last_redis_error = None

        if self.strategy == 'stateless':
            try:
                self.main_regex = re.compile(self.config['regex'])
            except re.error as e:
                logger.error(f"Invalid regex in rule '{self.config.get('pattern_name', 'unknown')}': {e}")
                self.main_regex = None
                self.disabled = True

        elif self.strategy in ['multi_match', 'stateful']:
            self.patterns = [] if self.strategy == 'multi_match' else []
            self.sub_patterns = [] if self.strategy == 'stateful' else []

            source_patterns = self.config.get('patterns', [])
            target_list = self.patterns if self.strategy == 'multi_match' else self.sub_patterns

            for p in source_patterns:
                try:
                    compiled = re.compile(p['regex'])
                except re.error as e:
                    logger.error(f"Invalid regex in rule '{self.config.get('pattern_name', 'unknown')}': {e}")
                    continue

                target_list.append({
                    "name": p.get('name'),
                    "regex": compiled,
                    "mapping": p.get('mapping', {}),
                    "static": p.get('static', {})
                })

            if self.strategy == 'stateful':
                try:
                    self.id_regex = re.compile(self.config['id_regex'])
                except re.error as e:
                    logger.error(f"Invalid id_regex in rule '{self.config.get('pattern_name', 'unknown')}': {e}")
                    self.id_regex = None

    def process(self, log_input):
        if self.disabled:
            return None
        if self.strategy == 'stateless':
            return self._process_stateless(log_input)
        elif self.strategy == 'multi_match':
            return self._process_multi_match(log_input)
        elif self.strategy == 'stateful':
            return self._process_stateful(log_input)
        elif self.strategy == 'json_map':
            return self._process_json_map(log_input)
        elif self.strategy == 'xml_xpath':
            return self._process_xml(log_input)

    def _process_xml(self, log_input):
        try:
            # Parse the raw XML string
            root = ET.fromstring(log_input.raw)
        except ET.ParseError:
            return None

        events = []
        items_path = self.config.get('items_xpath', '.')
        mapping = self.config.get('mapping', {})
        static = self.config.get('static', {})

        for item in root.findall(items_path):
            event = self._init_event(log_input)
            
            for source_path, target_field in mapping.items():
                val = None
                # Support extracting attributes (e.g., nvt/@oid)
                if '/@' in source_path:
                    tag, attr = source_path.split('/@')
                    child = item.find(tag)
                    if child is not None:
                        val = child.get(attr)
                else:
                    child = item.find(source_path)
                    if child is not None and child.text:
                        val = child.text.strip()

                if val:
                    target, dtype = target_field.split('|') if '|' in target_field else (target_field, 'str')
                    if dtype == 'float':
                        try: val = float(val)
                        except ValueError: pass
                    elif dtype == 'int':
                        try: val = int(float(val))
                        except ValueError: pass

                    self._set_nested(event, target, val)

            self._apply_static(event, static)
            enriched = self._enrich_event(event)
            if enriched:
                events.append(enriched)

        return events

    def _process_json_map(self, log_input):
        try:
            data = json.loads(log_input.raw)
        except json.JSONDecodeError:
            return None

        event = self._init_event(log_input)
        mapping = self.config.get('mapping', {})

        for source_path, target_field in mapping.items():
            value = self._get_path(data, source_path)
            if value is not None:
                if isinstance(value, list) and len(value) > 0:
                    value = [v for v in value if v is not None]
                    if not value: continue
                self._set_nested(event, target_field, value)

        self._apply_static(event, self.config.get('static', {}))
        return self._enrich_event(event)

    def _get_path(self, data, path):
        keys = path.split('.')
        current = data
        for i, k in enumerate(keys):
            if k == '*':
                if isinstance(current, list):
                    remaining_path = '.'.join(keys[i+1:])
                    return [self._get_path(item, remaining_path) for item in current]
                else:
                    return None
            try:
                if isinstance(current, list):
                    k = int(k)
                current = current[k]
            except (KeyError, IndexError, ValueError, TypeError):
                return None
        return current

    def _enrich_event(self, event):
        if not event: return None
        source_ip = event.get('source', {}).get('ip')
        if source_ip:
            geo = self.geoip.enrich(source_ip)
            if geo:
                if 'source' not in event:
                    event['source'] = {}
                event['source']['geo'] = geo
        return event

    def _process_stateless(self, log_input):
        match = self.main_regex.search(log_input.raw)
        if not match: return None

        event = self._init_event(log_input)
        self._map_fields(event, match.groupdict(), self.config.get('mapping', {}))
        self._apply_static(event, self.config.get('static', {}))
        return self._enrich_event(event)

    def _process_multi_match(self, log_input):
        for p in self.patterns:
            match = p['regex'].search(log_input.raw)
            if match:
                event = self._init_event(log_input)
                self._map_fields(event, match.groupdict(), p['mapping'])
                self._apply_static(event, p.get('static', {}))
                return self._enrich_event(event)
        return None

    def _process_stateful(self, log_input):
        self.last_redis_error = None

        # Fallback-only when id_regex is missing or invalid
        if not self.id_regex:
            for p in self.sub_patterns:
                m = p['regex'].search(log_input.raw)
                if m:
                    event = self._init_event(log_input)
                    self._map_fields(event, m.groupdict(), p['mapping'])
                    self._apply_static(event, p.get('static', {}))
                    return self._enrich_event(event)

            return None

        match = self.id_regex.search(log_input.raw)

        # Stateless fallback for non-ID logs
        if not match:
            for p in self.sub_patterns:
                m = p['regex'].search(log_input.raw)
                if m:
                    event = self._init_event(log_input)
                    self._map_fields(event, m.groupdict(), p['mapping'])
                    self._apply_static(event, p.get('static', {}))
                    return self._enrich_event(event)

            return None

        trx_id = match.group('id')
        redis_key = f"state:{trx_id}"
        try:
            state = r.get(redis_key)
        except Exception as e:
            self.last_redis_error = f"redis_get_failed: {e}"
            return None

        event = json.loads(state) if state else self._init_event(log_input)
        if not state:
            event['event']['id'] = trx_id
            event['raw_buffer'] = []
            event['_metadata'] = log_input.meta

        event['raw_buffer'].append(log_input.raw)

        for p in self.sub_patterns:
            m = p['regex'].search(log_input.raw)
            if m:
                self._map_fields(event, m.groupdict(), p['mapping'])
                self._apply_static(event, p.get('static', {}))

        if self.config['end_signal'] in log_input.raw:
            try:
                r.delete(redis_key)
            except Exception as e:
                self.last_redis_error = f"redis_delete_failed: {e}"
            event['event']['original'] = "\n".join(event['raw_buffer'])
            if 'raw_buffer' in event:
                del event['raw_buffer']
            if '_metadata' in event:
                del event['_metadata']
            return self._enrich_event(event)
        else:
            try:
                r.set(redis_key, json.dumps(event), ex=300)
            except Exception as e:
                self.last_redis_error = f"redis_set_failed: {e}"
                return None
            return None

    def _init_event(self, log_input):
        base_event = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "event": {"module": log_input.program},
            "observer": log_input.meta
        }
        
        # Only include the raw log string if it's NOT an XML file
        if self.strategy != 'xml_xpath':
            base_event["event"]["original"] = log_input.raw
            
        return base_event

    def _map_fields(self, event, data, mapping):
        for k, v in data.items():
            if k in mapping:
                target, dtype = mapping[k].split('|') if '|' in mapping[k] else (mapping[k], 'str')
                val = int(v) if dtype == 'int' and v.isdigit() else v
                self._set_nested(event, target, val)

    def _apply_static(self, event, static):
        if static:
            for k, v in static.items():
                self._set_nested(event, k, v)

    def _set_nested(self, d, path, value):
        keys = path.split('.')
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        last = keys[-1]
        if last in d:
            if isinstance(d[last], list):
                if isinstance(value, list):
                    for v in value:
                        if v not in d[last]: d[last].append(v)
                elif value not in d[last]:
                    d[last].append(value)
            else:
                if d[last] != value:
                    d[last] = [d[last]]
                    if isinstance(value, list):
                        d[last].extend(value)
                    else:
                        d[last].append(value)
        else:
            d[last] = value
