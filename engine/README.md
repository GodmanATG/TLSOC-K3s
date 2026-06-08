# FOSS SOC Engine

A high-performance, polymorphic log parsing and normalization engine designed for Security Operations Centers (SOC).

The FOSS SOC Engine consumes raw logs from Kafka, dynamically routes them to the correct parser based on log metadata, and normalizes them into structured, ECS-compliant JSON. It supports stateless regex parsing, stateful multi-line log reassembly, and direct JSON field mapping for high-throughput environments.

---

## Core Capabilities

### Polymorphic Routing
Decouples log sources from parsing logic. Multiple source programs (for example, `mail_auth`, `web_auth`, `linux_auth`) can be routed to a single reusable rule definition via configuration-based program mapping.

### Stateful Parsing
Reassembles fragmented or multi-line logs (such as Postfix email transactions) into a single coherent event using Redis-backed correlation with TTL-based cleanup.

### Hybrid Parsing Strategies

The engine supports multiple parsing strategies selectable per rule:

- **stateless**  
  Standard regex-based parsing for single-line logs  
  Examples: Apache, Nginx access logs

- **multi_match**  
  Sequential evaluation of multiple regex patterns  
  Examples: Linux authentication logs, SSH, sudo, cron

- **stateful**  
  Correlates multiple log lines using transaction identifiers  
  Examples: Postfix mail flow, WAF transaction logs

- **json_map**  
  High-speed direct mapping of JSON logs using dot-path notation with wildcard support  
  Examples: ModSecurity, structured application logs, cloud audit logs

### Auto-Enrichment
Automatically enriches events with GeoIP metadata (city, country, latitude, longitude) for public IP addresses.

### Resilience and Observability
- Dead Letter Queue (DLQ) for logs that fail parsing
- Continuous health monitoring with throughput (EPS), error rate, and uptime tracking

---

## Prerequisites

### Software
- Python 3.8+
- Apache Kafka (input source)
- Redis (required for stateful parsing)

### System
- Linux environment  
  Ubuntu / Debian recommended for systemd service integration

---

## Installation
### GeoIP Database Requirement

This project uses the MaxMind GeoLite2 City database for IP enrichment.

Due to MaxMind licensing restrictions, the database is **not stored in the repository**.
Instead, it is automatically downloaded during installation.

Before running `install.sh`, export your MaxMind license key:

```bash
export MAXMIND_LICENSE_KEY=YOUR_MAXMIND_KEY
```

### 1. Clone the Repository

```bash
git clone https://github.com/sankettaware16/foss-soc-engine.git
sudo mv foss-soc-engine /etc/
cd /etc/foss-soc-engine
```
2. Run the Installer

The installer performs the following:

Installs Python dependencies

Creates runtime directories (logs/, database/)

Sets required permissions
```
chmod +x install.sh
./install.sh
```
3. Configure GeoIP Database

The engine requires the MaxMind GeoLite2 City database.

Download GeoLite2-City.mmdb from MaxMind

Place it in the database/ directory or you it can be directly installed using install.sh if you provide keys to it

```
mv /path/to/GeoLite2-City.mmdb ./database/
```

Configuration

Edit config.yaml to match your environment.
```
kafka:
  bootstrap_servers: ["localhost:9092"]
  input_topic: "^(syslog|waf-logs|.*)$"
  group_id: "soc-parser-v1"

paths:
  output_dir: "/var/log/soc_output/"
  rules_dir: "rules/"

program_mapping:
  ssh_server: "linux_auth"
  ftp_server: "linux_auth"
  modsec_audit: "modsec"

```
install and setup redis
```
sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server
```
create log dir
```
sudo mkdir -p /var/log/soc_output/
sudo chown -R username:username /var/log/soc_output/ #if required
```
Program mapping allows multiple source programs to reuse a single rule definition.

Usage
Manual Execution (Debug / Development)

Run the engine in the foreground:
```
sudo python3 main.py
```
Running as a System Service (Production)

Generate and enable the systemd service:
```
sudo ./setup_service.sh
```

Check service status:
```
sudo systemctl status foss-soc
```

View live logs:
```
journalctl -u foss-soc -f
```
Development and Testing
Interactive Rule Tester

Test regex patterns and JSON mappings without Kafka ingestion:
```
python3 test_rules.py
```

Options:

Auto-detect: scans all rules to find a matching parser

Explicit parser selection for targeted testing

File-Based Testing

Process a file containing raw logs to validate bulk parsing behavior:
```
python3 test_file.py sample_logs.txt postfix
```

Auto-detect the best rule per line:
```
python3 test_file.py sample_logs.txt AUTO
```

Dump matched events as JSON:
```
python3 test_file.py sample_logs.txt postfix --show-success
```

Dump every parsed line with the matched rule:
```
python3 test_file.py sample_logs.txt AUTO --show-parsed
```

Dump every unparsed line with the reason (`no_match`, `buffered`, `errors`):
```
python3 test_file.py sample_logs.txt AUTO --show-unparsed
```

Tune sample size for each unparsed bucket in the summary:
```
python3 test_file.py sample_logs.txt postfix --samples 20
```

The script prints a summary with parsed vs unparsed counts, plus reason buckets
(`no_match`, `buffered`, `errors`) and sample lines for each bucket. In AUTO mode,
it also shows a per-rule parsed count to help identify gaps.

```
Directory Structure
├── config.yaml          # Main runtime configuration
├── core/
│   ├── engine.py        # Parsing strategies and execution engine
│   ├── registry.py     # Rule loading and routing logic
│   └── schema.py       # Input validation and normalization
├── rules/              # YAML parsing rule definitions
│   ├── apache.yaml
│   ├── linux_auth.yaml
│   ├── modsec.yaml
│   └── postfix.yaml
├── logs/               # Runtime logs
│   ├── engine.log      # Engine logs
│   ├── dlq.json        # Dead Letter Queue
│   └── stats.json      # Health metrics
└── main.py             # Application entry point
```
Monitoring

The engine writes health metrics every 60 seconds to logs/stats.json.
This file can be ingested by external monitoring or SIEM agents (Filebeat, Wazuh).

Example:
```
{
  "timestamp": "2026-01-27T10:00:00",
  "uptime_sec": 3600,
  "eps": 450.5,
  "total_processed": 1500000,
  "errors_last_min": 0
}
```
Writing and Updating Parsing Rules

Use this section when you need to support a new log source or refine an existing parser.

Quick steps
1. Create or edit a YAML file in the rules/ directory.
2. Set `pattern_name` (must be unique). If missing, the file name is used.
3. Choose a `strategy` (see below) and define its fields.
4. Add `mapping` (and optional `static`) to normalize fields.
5. Map the source program to the rule in config.yaml.
6. Restart the service (or restart the process).

```
sudo systemctl restart foss-soc
```

Choosing the best strategy
- stateless: Best for consistent single-line logs (access logs, IDS alerts).
- multi_match: Best when one source emits multiple line formats (auth, ssh, sudo).
- stateful: Best for multi-line transactions correlated by ID. Also supports non-ID lines
  through pattern fallback (connect, TLS, disconnect, NOQUEUE).
- json_map: Best when raw logs are already JSON (WAF, cloud audit, app logs).
- xml_xpath: Best when raw logs are XML (scanner exports, XML audit feeds).

Common fields
- pattern_name: Name of the rule (used by program mapping).
- strategy: One of stateless, multi_match, stateful, json_map, xml_xpath.
- mapping: Maps regex group names or JSON/XML paths to ECS-like targets.
- static: Fixed fields added to every event.
- regex: Required for stateless.
- patterns: Required for multi_match and stateful.
- id_regex, end_signal: Required for stateful.
- items_xpath: Required for xml_xpath.

Mapping syntax notes
- Regex strategies support type suffix: `field.path|int` or `field.path|float`.
- json_map uses dot paths and supports wildcards with `*` (returns a list).
- xml_xpath uses ElementTree paths and supports attributes via `/@`.
- Repeated mappings to the same field are merged into lists automatically.

Examples

Stateless (single regex)
```yaml
pattern_name: "apache_access"
strategy: "stateless"
regex: '(?P<ip>[\d\.]+) - - \[(?P<timestamp>[^\]]+)\] "(?P<method>\w+) (?P<path>[^\?\s]+)(?:\?(?P<query>[^\s]+))? HTTP/(?P<http_version>[\d\.]+)" (?P<status>\d+) (?P<body_bytes>\d+) "(?P<referrer>[^"]*)" "(?P<user_agent>[^"]*)"'
mapping:
  ip: "source.ip"
  method: "http.request.method"
  status: "http.response.status_code|int"
  body_bytes: "http.response.body.bytes|int"
  user_agent: "user_agent.original"
  referrer: "http.request.referrer"
  path: "url.path"
  query: "url.query"
static:
  event.category: "web"
```

Multi-match (multiple regexes)
```yaml
pattern_name: "linux_auth"
strategy: "multi_match"
patterns:
  - name: "ssh_success"
    regex: 'sshd\[\d+\]: Accepted password for (?P<user>\w+) from (?P<ip>[\d\.]+) port (?P<port>\d+)'
    mapping:
      user: "user.name"
      ip: "source.ip"
      port: "source.port"
    static:
      event.action: "login"
      event.outcome: "success"

  - name: "ssh_failed"
    regex: 'sshd\[\d+\]: Failed password for (invalid user )?(?P<user>\w+) from (?P<ip>[\d\.]+)'
    mapping:
      user: "user.name"
      ip: "source.ip"
    static:
      event.action: "login"
      event.outcome: "failure"
```

Stateful (transaction correlation with fallback)
```yaml
pattern_name: "postfix"
strategy: "stateful"
id_regex: '(?P<id>[A-Z0-9]{10,12}):'
end_signal: "removed"
patterns:
  - regex: 'client=(?P<host>.*?)\[(?P<ip>[\d\.]+)\]'
    mapping: { "ip": "source.ip" }

  - regex: 'from=<(?P<sender>[^@]+@(?P<s_domain>example\.com))>'
    mapping: { "sender": "email.from", "s_domain": "email.sender_domain" }
    static: { "email.sender_type": "internal" }

  # This will still parse connect/TLS/NOQUEUE lines without a queue id
  - regex: 'connect from (?P<host>.*?)\[(?P<ip>[\d\.]+)\]'
    mapping: { "ip": "source.ip" }
```

JSON map (direct field mapping)
```yaml
pattern_name: "modsec"
strategy: "json_map"
mapping:
  transaction.client_ip: "source.ip"
  transaction.request.method: "http.request.method"
  transaction.messages.*.details.ruleId: "rule.id"
  transaction.messages.*.message: "event.reason"
static:
  event.kind: "alert"
  event.category: "web"
  event.type: "waf"
```

XML XPath (structured XML)
```yaml
pattern_name: "openvas"
strategy: "xml_xpath"
items_xpath: ".//result"
mapping:
  nvt/@oid: "vulnerability.id"
  host: "destination.ip"
  severity: "event.severity|float"
static:
  event.category: "vulnerability"
```

Hooking a source program to a rule
Add a program mapping in config.yaml:
```yaml
program_mapping:
  postfix: "postfix"
  nginx_access_log: "apache_access"
  modsecurity_log: "modsec"
```

Testing your rule
- Interactive: `python3 test_rules.py`
- File-based: `python3 test_file.py sample_logs.txt AUTO`


