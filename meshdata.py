import configparser
import mysql.connector
import datetime
import json
import time
import utils
import logging
import re
from timezone_utils import time_ago  # Import time_ago from timezone_utils
from meshtastic_support import get_hardware_model_name  # Import get_hardware_model_name from meshtastic_support
import types


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode("utf-8")  # Convert bytes to string
        elif isinstance(obj, datetime.datetime):
            return obj.isoformat()  # Convert datetime to ISO format
        elif isinstance(obj, datetime.date):
            return obj.isoformat()  # Convert date to string
        elif isinstance(obj, set):
            return list(obj)  # Convert set to list
        # Use default serialization for other types
        return super().default(obj)


class MeshData:
    def __init__(self):
        config = configparser.ConfigParser()
        config.read('config.ini')
        self.config = config
        self.db = None
        self.connect_db()

    def __del__(self):
        if self.db:
            self.db.close()

    def int_id(self, id):
        try:
            id = id.replace("!", "")
            return int(f"0x{id}", 16)
        except Exception as e:
            pass
        return None

    def hex_id(self, id):
        return utils.convert_node_id_from_int_to_hex(id)

    def unknown(self, id):
        hexid = self.hex_id(id)
        short_name = hexid[-4:]
        long_name = f"Meshtastic {short_name}"
        return {
            "from": id,
            "decoded": {
                "json_payload": {
                    "long_name": long_name,
                    "short_name": short_name
                }
            }
        }

    def connect_db(self):
        max_retries = 5
        retry_delay = 10  # seconds
        
        for attempt in range(max_retries):
            try:
                # Ensure any existing connection is closed before creating a new one
                if self.db and self.db.is_connected():
                    try:
                        self.db.close()
                        logging.debug("Closed existing DB connection before reconnecting.")
                    except mysql.connector.Error as close_err:
                            logging.warning(f"Error closing existing DB connection: {close_err}")

                self.db = mysql.connector.connect(
                    host=self.config["database"]["host"],
                    user=self.config["database"]["username"],
                    password=self.config["database"]["password"],
                    database=self.config["database"]["database"],
                    charset="utf8mb4",
                    # Add connection timeout (e.g., 10 seconds)
                    connection_timeout=10
                )
                if self.db.is_connected():
                    cur = self.db.cursor()
                    cur.execute("SET NAMES utf8mb4;")
                    cur.close()
                    logging.info(f"Database connection successful (Attempt {attempt + 1}).")
                    return
                else:
                    # This case might not be reached if connect throws error, but good practice
                    raise mysql.connector.Error("Connection attempt returned but not connected.")

            except mysql.connector.Error as err:
                logging.warning(f"Database connection attempt {attempt + 1}/{max_retries} failed: {err}")
                if attempt < max_retries - 1:
                    logging.info(f"Retrying connection in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error("Maximum database connection retries reached. Raising error.")
                    raise # Re-raise the last error after all retries fail

    def ping_db(self):
        """Checks connection and attempts reconnect if needed."""
        if self.db is None:
            logging.warning("Database object is None. Attempting reconnect.")
            try:
                self.connect_db()
                return self.db is not None and self.db.is_connected()
            except Exception as e:
                logging.error(f"Reconnect failed during ping: {e}")
                return False

        try:
            # Check if connected first, then try ping with reconnect=True
            if not self.db.is_connected():
                    logging.warning("DB connection reported as not connected. Attempting ping/reconnect.")
            # The ping=True argument attempts to reconnect if connection is lost.
            self.db.ping(reconnect=True, attempts=3, delay=2)
            logging.debug("Database connection verified via ping.")
            return True
        except mysql.connector.Error as err:
            logging.error(f"Database ping/reconnect failed: {err}")
            # Attempt a full reconnect as a final measure if ping fails
            try:
                logging.warning("Ping failed. Attempting full database reconnect...")
                self.connect_db() # Use the existing connect method
                # Check connection status again after attempting connect_db
                if self.db and self.db.is_connected():
                    logging.info("Full database reconnect successful.")
                    return True
                else:
                    logging.error("Full database reconnect attempt failed to establish connection.")
                    return False
            except Exception as e:
                    logging.error(f"Full database reconnect attempt raised an exception: {e}")
                    return False

    def get_telemetry(self, id):
        telemetry = {}
        sql = """SELECT * FROM telemetry WHERE id = %s
AND battery_level IS NOT NULL
ORDER BY telemetry_time DESC LIMIT 1"""
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if row:
            column_names = [desc[0] for desc in cur.description]
            for i in range(1, len(row)):
                if isinstance(row[i], datetime.datetime):
                    telemetry[column_names[i]] = row[i].timestamp()
                else:
                    telemetry[column_names[i]] = row[i]
        cur.close()
        return telemetry

    def get_telemetry_all(self):
        telemetry = []
        sql = """SELECT * FROM telemetry
ORDER BY ts_created DESC LIMIT 1000"""
        cur = self.db.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            record = {}
            column_names = [desc[0] for desc in cur.description]
            for i in range(0, len(row)):
                if isinstance(row[i], datetime.datetime):
                    record[column_names[i]] = row[i].timestamp()
                else:
                    record[column_names[i]] = row[i]
            telemetry.append(record)
        cur.close()
        return telemetry

    def get_node_telemetry(self, node_id):
        telemetry = []
        sql = """SELECT * FROM telemetry
WHERE ts_created >= NOW() - INTERVAL 1 DAY
AND id = %s AND battery_level IS NOT NULL
ORDER BY ts_created"""
        params = (node_id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        for row in rows:
            record = {}
            column_names = [desc[0] for desc in cur.description]
            for i in range(0, len(row)):
                if isinstance(row[i], datetime.datetime):
                    record[column_names[i]] = row[i].timestamp()
                else:
                    record[column_names[i]] = row[i]
            telemetry.append(record)
        cur.close()
        return telemetry

    def get_position(self, id):
        position = {}
        sql = "SELECT * FROM position WHERE id = %s"
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if row:
            column_names = [desc[0] for desc in cur.description]
            for i in range(1, len(row)):
                if isinstance(row[i], datetime.datetime):
                    position[column_names[i]] = row[i].timestamp()
                else:
                    position[column_names[i]] = row[i]
        cur.close()
        return position

    def get_position_at_time(self, node_id, target_timestamp, cur=None):
        """Retrieves the position record from positionlog for a node that is closest to, but not after, the target timestamp."""
        position = {}
        close_cur = False
        if cur is None:
            cur = self.db.cursor(dictionary=True)
            close_cur = True
        try:
            target_dt = datetime.datetime.fromtimestamp(target_timestamp)
            sql = """SELECT latitude_i, longitude_i, ts_created
                     FROM positionlog
                     WHERE id = %s
                     ORDER BY ABS(TIMESTAMPDIFF(SECOND, ts_created, %s)) ASC
                     LIMIT 1"""
            params = (node_id, target_dt)
            cur.execute(sql, params)
            row = cur.fetchone()
            if row:
                position = {
                    "latitude_i": row["latitude_i"],
                    "longitude_i": row["longitude_i"],
                    "position_time": row["ts_created"].timestamp() if isinstance(row["ts_created"], datetime.datetime) else row["ts_created"],
                    "latitude": row["latitude_i"] / 10000000 if row["latitude_i"] else None,
                    "longitude": row["longitude_i"] / 10000000 if row["longitude_i"] else None
                }
        except mysql.connector.Error as err:
            logging.error(f"Database error fetching nearest position for {node_id}: {err}")
        except Exception as e:
            logging.error(f"Error fetching nearest position for {node_id}: {e}")
        finally:
            if close_cur:
                cur.close()
        return position

    def get_neighbors(self, id):
        neighbors = []
        sql = """SELECT
    a.id,
    a.neighbor_id,
    a.snr,
    p1.latitude_i lat1_i,
    p1.longitude_i lon1_i,
    p2.latitude_i lat2_i,
    p2.longitude_i lon2_i
FROM neighborinfo a
LEFT OUTER JOIN position p1 ON p1.id = a.id
LEFT OUTER JOIN position p2 ON p2.id = a.neighbor_id
WHERE a.id = %s
AND a.ts_created >= NOW() - INTERVAL 1 DAY
"""
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        column_names = [desc[0] for desc in cur.description]
        for row in rows:
            record = {}
            for i in range(1, len(row)):
                if isinstance(row[i], datetime.datetime):
                    record[column_names[i]] = row[i].timestamp()
                else:
                    record[column_names[i]] = row[i]

            if record["lat1_i"] and record["lon1_i"] and \
                    record["lat2_i"] and record["lon2_i"]:
                distance = round(utils.distance_between_two_points(
                    record["lat1_i"] / 10000000,
                    record["lon1_i"] / 10000000,
                    record["lat2_i"] / 10000000,
                    record["lon2_i"] / 10000000
                ), 2)
            else:
                distance = None
            record["distance"] = distance
            del record["lat1_i"]
            del record["lon1_i"]
            del record["lat2_i"]
            del record["lon2_i"]
            neighbors.append(record)
        cur.close()
        return neighbors

    def get_traceroutes(self, page=1, per_page=25):
        """Get paginated traceroutes with SNR information, grouping all attempts (request and reply) together."""
        page = max(1, page)  # Ensure page is at least 1
        cur = self.db.cursor()
        cur.execute("SELECT COUNT(DISTINCT request_id) FROM traceroute")
        total = cur.fetchone()[0]
        
        # Get paginated request_ids
        cur.execute("""
            SELECT request_id
            FROM traceroute
            GROUP BY request_id
            ORDER BY MAX(ts_created) DESC
            LIMIT %s OFFSET %s
        """, (per_page, (page - 1) * per_page))
        request_ids = [row[0] for row in cur.fetchall()]
        
        # Fetch all traceroute rows for these request_ids
        if not request_ids:
            return {
                "items": [],
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page,
                "has_prev": page > 1,
                "has_next": page * per_page < total,
                "prev_num": page - 1,
                "next_num": page + 1
            }
        format_strings = ','.join(['%s'] * len(request_ids))
        sql = f"""
            SELECT 
                t.request_id,
                t.from_id,
                t.to_id,
                t.route,
                t.route_back,
                t.snr_towards,
                t.snr_back,
                t.success,
                t.channel,
                t.hop_limit,
                t.ts_created,
                t.is_reply,
                t.error_reason,
                t.attempt_number,
                t.traceroute_id
            FROM traceroute t
            WHERE t.request_id IN ({format_strings})
            ORDER BY t.request_id DESC, t.ts_created ASC
        """
        cur.execute(sql, tuple(request_ids))
        rows = cur.fetchall()
        
        # Group by request_id
        from collections import defaultdict
        grouped = defaultdict(list)
        for row in rows:
            route = [int(a) for a in row[3].split(";")] if row[3] else []
            route_back = [int(a) for a in row[4].split(";")] if row[4] else []
            snr_towards = [float(s)/4.0 for s in row[5].split(";")] if row[5] else []
            snr_back = [float(s)/4.0 for s in row[6].split(";")] if row[6] else []
            from_id = row[1]
            to_id = row[2]
            # For zero-hop, do NOT set route or route_back to endpoints; leave as empty lists
            grouped[row[0]].append({
                "id": row[14],  # Use traceroute_id as the unique id
                "from_id": from_id,
                "to_id": to_id,
                "route": route,
                "route_back": route_back,
                "snr_towards": snr_towards,
                "snr_back": snr_back,
                "success": row[7],
                "channel": row[8],
                "hop_limit": row[9],
                "ts_created": row[10].timestamp(),
                "is_reply": row[11],
                "error_reason": row[12],
                "attempt_number": row[13],
                "traceroute_id": row[14]
            })
        # Prepare items as a list of dicts, each with all attempts for a request_id
        items = []
        for req_id in request_ids:
            attempts = grouped[req_id]
            # Group status logic: prefer success, then error, then incomplete
            summary = dict(attempts[0])
            summary['attempts'] = attempts
            summary['success'] = any(a['success'] for a in attempts)
            summary['error_reason'] = next((a['error_reason'] for a in attempts if a['error_reason']), None)
            # If any attempt is successful, set status to success
            if summary['success']:
                summary['status'] = 'success'
            elif summary['error_reason']:
                summary['status'] = 'error'
            else:
                summary['status'] = 'incomplete'
            items.append(summary)
        cur.close()
        return {
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
            "has_prev": page > 1,
            "has_next": page * per_page < total,
            "prev_num": page - 1,
            "next_num": page + 1
        }

    def iter_pages(current_page, total_pages, left_edge=2, left_current=2, right_current=2, right_edge=2):
        """Helper function to generate page numbers for pagination."""
        last = 0
        for num in range(1, total_pages + 1):
            if (num <= left_edge or
                (current_page - left_current - 1 < num < current_page + right_current) or
                num > total_pages - right_edge):
                if last + 1 != num:
                    yield None
                yield num
                last = num

    def get_nodes(self, active=False):
        """
        Retrieve all nodes from the database, including their latest telemetry, position, and channel data.

        This method uses a single optimized SQL query with Common Table Expressions (CTEs) to join the latest telemetry,
        position, and channel information for each node. To avoid column name collisions (especially for the 'id' field),
        all joined tables alias their 'id' columns (e.g., 'telemetry_id', 'position_id', 'channel_id') and only the
        primary node ID from 'nodeinfo' is selected as 'id'.

        This explicit column selection and aliasing is critical: if the joined tables' 'id' columns were not aliased or
        omitted from the SELECT list, they could overwrite the real node ID in the result set with NULL for nodes that
        lack telemetry or position data. This would cause many nodes to be skipped in the final output, leading to an
        incorrect and reduced node count. By selecting only 'n.id' as 'id', we ensure all nodes from 'nodeinfo' are
        included, regardless of whether they have telemetry, position, or channel data.

        Args:
            active (bool): Unused, present for compatibility.
        Returns:
            dict: A dictionary of nodes keyed by their hex ID, with all relevant data included.
        """
        nodes = {}
        active_threshold = int(
            self.config["server"]["node_activity_prune_threshold"]
        )
        
        # Combined query to get all node data in one go
        sql = """
        WITH latest_telemetry AS (
            SELECT id as telemetry_id, 
                   air_util_tx,
                   battery_level,
                   channel_utilization,
                   uptime_seconds,
                   voltage,
                   temperature,
                   relative_humidity,
                   barometric_pressure,
                   gas_resistance,
                   current,
                   telemetry_time,
                   channel as telemetry_channel,
                   ROW_NUMBER() OVER (PARTITION BY id ORDER BY telemetry_time DESC) as rn
            FROM telemetry
            WHERE battery_level IS NOT NULL
        ),
        latest_position AS (
            SELECT id as position_id,
                   altitude,
                   ground_speed,
                   ground_track,
                   latitude_i,
                   longitude_i,
                   location_source,
                   precision_bits,
                   position_time,
                   geocoded,
                   ROW_NUMBER() OVER (PARTITION BY id ORDER BY position_time DESC) as rn
            FROM position
        ),
        latest_channel AS (
            SELECT id as channel_id, channel, ts_created
            FROM (
                SELECT id, channel, ts_created,
                       ROW_NUMBER() OVER (PARTITION BY id ORDER BY ts_created DESC) as rn
                FROM (
                    SELECT id, channel, ts_created
                    FROM telemetry
                    WHERE channel IS NOT NULL
                    UNION ALL
                    SELECT from_id as id, channel, ts_created
                    FROM text
                    WHERE channel IS NOT NULL
                ) combined
                WHERE id IS NOT NULL
            ) ranked
            WHERE rn = 1
        )
        SELECT 
            n.id,
            n.long_name,
            n.short_name,
            n.hw_model,
            n.role,
            n.firmware_version,
            n.owner,
            n.updated_via,
            n.ts_seen,
            n.ts_created,
            n.ts_updated,
            u.username as owner_username,
            CASE WHEN n.ts_seen > FROM_UNIXTIME(%s) THEN 1 ELSE 0 END as is_active,
            UNIX_TIMESTAMP(n.ts_uplink) as ts_uplink,
            -- Telemetry fields
            t.air_util_tx,
            t.battery_level,
            t.channel_utilization,
            t.uptime_seconds,
            t.voltage,
            t.temperature,
            t.relative_humidity,
            t.barometric_pressure,
            t.gas_resistance,
            t.current,
            t.telemetry_time,
            t.telemetry_channel,
            -- Position fields
            p.altitude,
            p.ground_speed,
            p.ground_track,
            p.latitude_i,
            p.longitude_i,
            p.location_source,
            p.precision_bits,
            p.position_time,
            p.geocoded,
            -- Channel
            c.channel
        FROM nodeinfo n
        LEFT OUTER JOIN meshuser u ON n.owner = u.email
        LEFT OUTER JOIN latest_telemetry t ON n.id = t.telemetry_id AND t.rn = 1
        LEFT OUTER JOIN latest_position p ON n.id = p.position_id AND p.rn = 1
        LEFT OUTER JOIN latest_channel c ON n.id = c.channel_id
        WHERE n.id IS NOT NULL
        """
        
        cur = self.db.cursor(dictionary=True)
        timeout = time.time() - active_threshold
        cur.execute(sql, (timeout,))
        rows = cur.fetchall()
        
        # print("Fetched rows:", len(rows))
        skipped = 0
        for row in rows:
            if not row or row.get('id') is None:
                skipped += 1
                continue
            if not row or row.get('id') is None:
                continue  # Skip rows with no ID
                
            record = {}
            # Convert datetime fields to timestamps
            for key, value in row.items():
                if isinstance(value, datetime.datetime):
                    record[key] = value.timestamp()
                else:
                    record[key] = value
            
            # Process telemetry data
            telemetry = types.SimpleNamespace()  # Use SimpleNamespace for attribute access
            telemetry_fields = [
                'air_util_tx', 'battery_level', 'channel_utilization',
                'uptime_seconds', 'voltage', 'temperature', 'relative_humidity',
                'barometric_pressure', 'gas_resistance', 'current',
                'telemetry_time', 'telemetry_channel'
            ]
            # Initialize all telemetry fields to None first
            for field in telemetry_fields:
                setattr(telemetry, field, None)
            # Then set values from row if they exist
            for field in telemetry_fields:
                if field in row and row[field] is not None:
                    setattr(telemetry, field, row[field])
            record['telemetry'] = telemetry
            
            # Process position data
            position = types.SimpleNamespace()  # Use SimpleNamespace for attribute access
            position_fields = [
                'altitude', 'ground_speed', 'ground_track', 'latitude_i',
                'longitude_i', 'location_source', 'precision_bits',
                'position_time', 'geocoded'
            ]
            # Initialize all position fields to None first
            for field in position_fields:
                setattr(position, field, None)
            # Then set values from row if they exist
            for field in position_fields:
                if field in row and row[field] is not None:
                    setattr(position, field, row[field])
            
            # Always set latitude and longitude attributes
            position.latitude = position.latitude_i / 10000000 if position.latitude_i else None
            position.longitude = position.longitude_i / 10000000 if position.longitude_i else None
            record['position'] = position
            
            # Get neighbors data
            record['neighbors'] = self.get_neighbors(row['id'])
            
            # Set other fields
            record['role'] = record.get('role', 0)
            record['active'] = bool(record.get('is_active', 0))
            record['last_seen'] = utils.time_since(record['ts_seen'])
            record['channel'] = row.get('channel')
            
            try:
                # Convert node ID to hex string and ensure it's properly formatted
                node_id_hex = utils.convert_node_id_from_int_to_hex(row['id'])
                if node_id_hex:  # Only add if conversion was successful
                    nodes[node_id_hex] = record
            except (TypeError, ValueError) as e:
                logging.error(f"Error converting node ID {row['id']} to hex: {e}")
                continue
        
        # print("Skipped rows due to missing id:", skipped)
        # print("Final nodes count:", len(nodes))
        cur.close()
        return nodes

    def get_chat(self, page=1, per_page=50):
        """Get paginated chat messages with reception data."""
        # Get total count first
        cur = self.db.cursor()
        cur.execute("SELECT COUNT(DISTINCT t.message_id) FROM text t")
        total = cur.fetchone()[0]
        
        # Get paginated results with reception data
        sql = """
        SELECT t.*,
            GROUP_CONCAT(
                CONCAT_WS(':', r.received_by_id, r.rx_snr, r.rx_rssi, r.hop_limit, r.hop_start)
                SEPARATOR '|'
            ) AS reception_data
        FROM text t
        LEFT JOIN message_reception r ON t.message_id = r.message_id
        GROUP BY t.message_id, t.from_id, t.to_id, t.text, t.ts_created, t.channel
        ORDER BY t.ts_created DESC
        LIMIT %s OFFSET %s
        """
        
        offset = (page - 1) * per_page
        cur = self.db.cursor()
        cur.execute(sql, (per_page, offset))
        rows = cur.fetchall()
        column_names = [desc[0] for desc in cur.description]
        
        chats = []
        prev_key = ""
        for row in rows:
            record = {}
            for i in range(len(row)):
                col = column_names[i]
                if isinstance(row[i], datetime.datetime):
                    record[col] = row[i].timestamp()
                else:
                    record[col] = row[i]
            
            # Parse reception information
            record["receptions"] = []
            receptions_str = record.get("reception_data")
            if receptions_str:
                for reception in receptions_str.split("|"):
                    if reception and reception.count(':') >= 2:
                        try:
                            parts = reception.split(":")
                            node_id = parts[0]
                            snr = parts[1]
                            rssi = parts[2]
                            hop_limit = parts[3] if len(parts) > 3 else None
                            hop_start = parts[4] if len(parts) > 4 else None
                            
                            reception_data = {
                                "node_id": int(node_id),
                                "rx_snr": float(snr),
                                "rx_rssi": int(rssi),
                                "hop_limit": int(hop_limit) if hop_limit and hop_limit != "None" else None,
                                "hop_start": int(hop_start) if hop_start and hop_start != "None" else None
                            }
                            record["receptions"].append(reception_data)
                        except (ValueError, TypeError):
                            continue
                            
            record["from"] = self.hex_id(record["from_id"])
            record["to"] = self.hex_id(record["to_id"])
            msg_key = f"{record['from']}{record['to']}{record['text']}{record['message_id']}"
            if msg_key != prev_key:
                chats.append(record)
                prev_key = msg_key
        
        cur.close()
        
        return {
            "items": chats,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
            "has_prev": page > 1,
            "has_next": page * per_page < total,
            "prev_num": page - 1,
            "next_num": page + 1
        }

    def get_route_coordinates(self, id):
        sql = """SELECT longitude_i, latitude_i
FROM positionlog WHERE id = %s
AND source = 'position'
ORDER BY ts_created DESC"""
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        coords = []
        for row in cur.fetchall():
            coords.append([
                row[0] / 10000000,
                row[1] / 10000000
            ])
        cur.close()
        return list(reversed(coords))

    def get_logs(self):
        logs = []
        sql = "SELECT * FROM meshlog ORDER BY ts_created DESC"
        cur = self.db.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        column_names = [desc[0] for desc in cur.description]
        for row in rows:
            record = {}
            for i in range(len(row)):
                col = column_names[i]
                if isinstance(row[i], datetime.datetime):
                    record[col] = row[i].timestamp()
                else:
                    record[col] = row[i]
            logs.append(record)
        return logs

    def get_latest_node(self):
        sql = """select id, ts_created from nodeinfo
where id <> 4294967295 order by ts_created desc limit 1"""
        cur = self.db.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        latest = {}
        if row:
            latest = {
                "id": row[0],
                "ts_created": row[1].timestamp()
            }
        cur.close()
        return latest

    def get_user(self, username):
        sql = "SELECT * FROM meshuser WHERE username=%s"
        params = (username, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        column_names = [desc[0] for desc in cur.description]
        record = {}
        if row:
            for i in range(len(row)):
                col = column_names[i]
                if isinstance(row[i], datetime.datetime):
                    record[col] = row[i].timestamp()
                else:
                    record[col] = row[i]
        cur.close()
        return record

    def update_geocode(self, id, lat, lon):
        if self.config["geocoding"]["enabled"] != "true":
            return
        update = False
        sql = """SELECT 1 FROM position WHERE
id=%s AND (geocoded IS NULL OR (latitude_i <> %s OR longitude_i <> %s))"""
        params = (
            id,
            lat,
            lon
        )
        cur = self.db.cursor()
        cur.execute(sql, params)
        update = True if cur.fetchone() else False
        geo = None
        cur.close()
        if not update:
            return

        latitude = lat / 10000000
        longitude = lon / 10000000
        geocoded = utils.geocode_position(
            self.config['geocoding']['apikey'],
            latitude,
            longitude
        )
        if geocoded and "display_name" in geocoded:
            geo = geocoded["display_name"]

        sql = """UPDATE position SET
latitude_i = %s,
longitude_i = %s,
geocoded = %s
WHERE id = %s
"""
        params = (
            lat,
            lon,
            geo,
            id
        )
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()

    def graph_nodes(self):
        graph_data = {
            "nodes": [],
            "edges": []
        }
        nodes = self.get_nodes()
        known_edges = []
        known_nodes = []
        for id, node in nodes.items():
            if id not in known_nodes:
                if "neighbors" not in node:
                    continue
                if not node["neighbors"]:
                    continue

                graph_data['nodes'].append(
                    {
                        "id": id,
                        "name": node['long_name'],
                        "short": node['short_name'],
                        "height": 30,
                        "stroke": None,
                        'fill': {"src": utils.graph_icon(node['long_name'])}
                    }
                )
                known_nodes.append(id)
                for neighbor in node['neighbors']:
                    neigbor_id = \
                        utils.convert_node_id_from_int_to_hex(
                            neighbor["neighbor_id"]
                        )
                    edge_key_1 = f"{id}.{neigbor_id}"
                    edge_key_2 = f"{neigbor_id}.{id}"
                    if edge_key_1 not in known_edges and \
                            edge_key_2 not in known_edges:
                        if neigbor_id in nodes:
                            graph_data["edges"].append(
                                {"from": id, "to": neigbor_id}
                            )
                            known_edges.append(edge_key_1)
                            known_edges.append(edge_key_2)
        for edge in graph_data["edges"]:
            to = edge['to']
            to_node = None
            if to in nodes:
                to_node = nodes[to]
            else:
                to_node = self.unknown(self.int_id(to))
            if to not in known_nodes:
                known_nodes.append(to)
                graph_data['nodes'].append(
                    {
                        "id": to,
                        "name": to_node['long_name'],
                        "short": to_node['short_name'],
                        "height": 30,
                        "stroke": None,
                        'fill': {"src": utils.graph_icon(to_node['long_name'])}
                    }
                )

        return graph_data

    def store_node(self, data):
        if not data:
            return
        payload = dict(data["decoded"]["json_payload"])
        expected = [
            "hw_model",
            "long_name",
            "short_name",
            "role",
            "firmware_version"
        ]
        for attr in expected:
            if attr not in payload:
                payload[attr] = None

        sql = """INSERT INTO nodeinfo (
    id,
    long_name,
    short_name,
    hw_model,
    role,
    firmware_version,
    ts_updated
)
VALUES (%s, %s, %s, %s, %s, %s, NOW())
ON DUPLICATE KEY UPDATE
long_name = VALUES(long_name),
short_name = VALUES(short_name),
hw_model = COALESCE(VALUES(hw_model), hw_model),
role = COALESCE(VALUES(role), role),
firmware_version = COALESCE(VALUES(firmware_version), firmware_version),
ts_updated = VALUES(ts_updated)"""
        values = (
            data["from"],
            payload["long_name"],
            payload["short_name"],
            payload["hw_model"],
            payload["role"],
            payload["firmware_version"]
        )
        cur = self.db.cursor()
        cur.execute(sql, values)
        self.db.commit()

    def store_position(self, data, source="position"):
        payload = dict(data["decoded"]["json_payload"])
        expected = [
            "altitude",
            "ground_speed",
            "ground_track",
            "latitude_i",
            "location_source",
            "longitude_i",
            "precision_bits",
            "time"
        ]
        if "position_precision" in payload:
            payload["precision_bits"] = payload["position_precision"]
        for attr in expected:
            if attr not in payload:
                payload[attr] = None
        if payload["latitude_i"] and payload["longitude_i"]:
            self.update_geocode(
                self.verify_node(data["from"]),
                payload["latitude_i"],
                payload["longitude_i"]
            )
        sql = """INSERT INTO position (
    id,
    altitude,
    ground_speed,
    ground_track,
    latitude_i,
    location_source,
    longitude_i,
    precision_bits,
    position_time,
    ts_updated
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FROM_UNIXTIME(%s), NOW())
ON DUPLICATE KEY UPDATE
altitude = VALUES(altitude),
ground_speed = VALUES(ground_speed),
ground_track = VALUES(ground_track),
latitude_i = VALUES(latitude_i),
location_source = VALUES(location_source),
longitude_i = VALUES(longitude_i),
precision_bits= VALUES(precision_bits),
position_time = VALUES(position_time),
ts_updated = VALUES(ts_updated)"""
        values = (
            self.verify_node(data["from"]),
            payload["altitude"],
            payload["ground_speed"],
            payload["ground_track"],
            payload["latitude_i"],
            payload["location_source"],
            payload["longitude_i"],
            payload["precision_bits"],
            payload["time"] or time.time()
        )
        cur = self.db.cursor()
        cur.execute(sql, values)
        self.log_position(
            data["from"],
            payload["latitude_i"],
            payload["longitude_i"],
            source
        )
        self.db.commit()

    def store_mapreport(self, data):
        self.store_node(data)
        self.store_position(data, "mapreport")

    def store_neighborinfo(self, data):
        node_id = self.verify_node(data["from"])
        payload = dict(data["decoded"]["json_payload"])
        if "neighbors" not in payload:
            return
        sql = "DELETE FROM neighborinfo WHERE id = %s"
        params = (node_id, )
        self.db.cursor().execute(sql, params)
        for neighbor in payload["neighbors"]:
            sql = """INSERT INTO neighborinfo
(id, neighbor_id, snr, ts_created) VALUES (%s, %s, %s, NOW())"""
            params = (
                node_id,
                self.verify_node(neighbor["node_id"]),
                neighbor["snr"] if "snr" in neighbor else None
            )
            self.db.cursor().execute(sql, params)
        self.db.commit()

    def store_traceroute(self, data):
        import logging
        from_id = self.verify_node(data["from"])
        to_id = self.verify_node(data["to"])
        payload = dict(data["decoded"]["json_payload"])

        # Always use the payload's request_id if present, else fallback to message id
        payload_request_id = data["decoded"].get("request_id")
        message_id = data.get("id")
        request_id = payload_request_id or message_id

        # Determine if this is a reply and get error reason
        is_reply = False
        error_reason = None

        # Check if this is a routing message with error
        if "error_reason" in payload:
            error_reason = payload["error_reason"]

        # Check if this is a route reply (legacy Meshtastic format)
        if "route_reply" in payload:
            is_reply = True
            payload = payload["route_reply"]
        elif "route_request" in payload:
            payload = payload["route_request"]

        # Robust reply detection: if payload_request_id is present and does not match message_id, treat as reply
        if payload_request_id and message_id and str(payload_request_id) != str(message_id):
            is_reply = True

        # Insert the traceroute row
        route = None
        snr_towards = None
        if "route" in payload:
            route = ";".join(str(r) for r in payload["route"])
        if "snr_towards" in payload:
            snr_towards = ";".join(str(float(s)) for s in payload["snr_towards"])
        route_back = None
        snr_back = None
        if "route_back" in payload:
            route_back = ";".join(str(r) for r in payload["route_back"])
        if "snr_back" in payload:
            snr_back = ";".join(str(float(s)) for s in payload["snr_back"])

        # Parse as lists for logic
        route_list = payload.get("route", [])
        route_back_list = payload.get("route_back", [])
        snr_towards_list = payload.get("snr_towards", [])
        snr_back_list = payload.get("snr_back", [])

        # Only mark as success if:
        # For zero-hop direct connection:
        if not route_list and not route_back_list:
            # Must have SNR values in either direction
            success = bool(snr_towards_list or snr_back_list)
        # For multi-hop:
        elif route_list and route_back_list:
            # Must have SNR values in both directions
            success = bool(snr_towards_list and snr_back_list)
        else:
            success = False

        # Now join for DB storage
        route = None
        snr_towards = None
        if "route" in payload:
            route = ";".join(str(r) for r in payload["route"])
        if "snr_towards" in payload:
            snr_towards = ";".join(str(float(s)) for s in payload["snr_towards"])
        route_back = None
        snr_back = None
        if "route_back" in payload:
            route_back = ";".join(str(r) for r in payload["route_back"])
        if "snr_back" in payload:
            snr_back = ";".join(str(float(s)) for s in payload["snr_back"])

        # --- FIX: Define attempt_number before use ---
        attempt_number = data.get("attempt_number") or payload.get("attempt_number")

        channel = data.get("channel", None)
        hop_limit = data.get("hop_limit", None)
        traceroute_time = payload.get("time", None)
        sql = """INSERT INTO traceroute
        (from_id, to_id, channel, hop_limit, success, request_id, route, route_back, 
        snr_towards, snr_back, time, ts_created, is_reply, error_reason, attempt_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FROM_UNIXTIME(%s), NOW(), %s, %s, %s)"""
        params = (
            from_id,
            to_id,
            channel,
            hop_limit,
            success,
            request_id,
            route,
            route_back,
            snr_towards,
            snr_back,
            traceroute_time,
            is_reply,
            error_reason,
            attempt_number
        )
        self.db.cursor().execute(sql, params)
        self.db.commit()

        # --- Robust consolidation logic: ensure all related rows use the same request_id ---
        cur = self.db.cursor()
        ids_to_check = set()
        if message_id:
            ids_to_check.add(message_id)
        if payload_request_id:
            ids_to_check.add(payload_request_id)
        if request_id:
            ids_to_check.add(request_id)
        # Find all request_ids for these ids (as traceroute_id or request_id)
        all_request_ids = set()
        for idval in ids_to_check:
            if idval:
                cur.execute("SELECT request_id FROM traceroute WHERE traceroute_id = %s OR request_id = %s", (idval, idval))
                all_request_ids.update([row[0] for row in cur.fetchall() if row[0]])
        if all_request_ids:
            canonical_request_id = min(all_request_ids)
            cur.execute(f"UPDATE traceroute SET request_id = %s WHERE request_id IN ({','.join(['%s']*len(all_request_ids))})", (canonical_request_id, *all_request_ids))
            self.db.commit()
            logging.info(f"Consolidated traceroute rows: set request_id={canonical_request_id} for ids {all_request_ids}")
        else:
            logging.warning(f"No related traceroute rows found for ids: {ids_to_check}")
        cur.close()

    def get_successful_traceroutes(self):
        sql = """
        SELECT 
            t.traceroute_id,
            t.from_id,
            t.to_id,
            t.route,
            t.route_back,
            t.snr_towards,
            t.snr_back,
            t.ts_created,
            n1.short_name as from_name,
            n2.short_name as to_name
        FROM traceroute t
        JOIN nodeinfo n1 ON t.from_id = n1.id
        JOIN nodeinfo n2 ON t.to_id = n2.id
        WHERE t.success = TRUE
        ORDER BY t.ts_created DESC
        """
        cur = self.db.cursor()
        cur.execute(sql)
        
        results = []
        column_names = [desc[0] for desc in cur.description]
        for row in cur.fetchall():
            result = dict(zip(column_names, row))
            # Convert timestamp to Unix timestamp if needed
            if isinstance(result['ts_created'], datetime.datetime):
                result['ts_created'] = result['ts_created'].timestamp()
            # Parse route and SNR data
            if result['route']:
                result['route'] = [int(a) for a in result['route'].split(";")]
            if result['route_back']:
                result['route_back'] = [int(a) for a in result['route_back'].split(";")]
            if result['snr_towards']:
                result['snr_towards'] = [float(s) for s in result['snr_towards'].split(";")]
            if result['snr_back']:
                result['snr_back'] = [float(s) for s in result['snr_back'].split(";")]
            results.append(result)
        
        cur.close()
        return results

    def store_telemetry(self, data):
        # Use class-level config that's already loaded
        retention_days = self.config.getint("server", "telemetry_retention_days", fallback=None) if self.config else None
        
        # Only check for cleanup every N records (using modulo on auto-increment ID or timestamp)
        cur = self.db.cursor()
        cur.execute("SELECT COUNT(*) FROM telemetry WHERE ts_created > DATE_SUB(NOW(), INTERVAL 1 HOUR)")
        recent_count = cur.fetchone()[0]
        
        # Only run cleanup if we have accumulated enough recent records
        if recent_count > 200:  # Adjust threshold as needed
            if retention_days:
                cur.execute("""DELETE FROM telemetry 
                    WHERE ts_created < DATE_SUB(NOW(), INTERVAL %s DAY)""", 
                    (retention_days,))
            else:
                # More efficient count-based cleanup
                cur.execute("""DELETE FROM telemetry WHERE ts_created <= 
                    (SELECT ts_created FROM 
                        (SELECT ts_created FROM telemetry ORDER BY ts_created DESC LIMIT 1 OFFSET 20000) t)""")
        
        cur.close()
        self.db.commit()

        node_id = self.verify_node(data["from"])
        payload = dict(data["decoded"]["json_payload"])

        # Extract channel from the root of the telemetry data
        channel = data.get("channel")

        def validate_telemetry_value(value, field_name=None):
            """Validate telemetry values, converting invalid values to None."""
            if value is None:
                return None
            try:
                # Convert to float and check if it's a valid number
                float_val = float(value)
                if float_val == float('inf') or float_val == float('-inf') or str(float_val).lower() == 'nan':
                    return None
                
                # Apply field-specific validation
                if field_name:
                    if field_name == 'battery_level':
                        # Battery level should be positive and an integer
                        # Allow values > 100% due to calibration issues
                        if float_val < 0 or not float_val.is_integer():
                            return None
                        return int(float_val)  # Convert to int since DB expects INT
                    elif field_name == 'air_util_tx' or field_name == 'channel_utilization':
                        # Utilization values should be positive
                        # Allow > 100% due to network conditions
                        if float_val < 0:
                            return None
                    elif field_name == 'uptime_seconds':
                        # Uptime should be positive
                        if float_val < 0:
                            return None
                    elif field_name == 'voltage':
                        # Voltage should be positive and reasonable
                        # Allow up to 100V to accommodate various power systems
                        if float_val < 0 or float_val > 100:
                            return None
                    elif field_name == 'temperature':
                        # Temperature should be within reasonable sensor range
                        # Allow -100°C to 150°C to accommodate various sensors and conditions
                        if not -100 <= float_val <= 150:
                            return None
                    elif field_name == 'relative_humidity':
                        # Humidity should be reasonable
                        # Allow slight overshoot due to calibration
                        if not -5 <= float_val <= 105:
                            return None
                    elif field_name == 'barometric_pressure':
                        # Pressure should be positive and reasonable
                        # Allow wider range for different altitudes and conditions
                        if float_val < 0 or float_val > 2000:
                            return None
                    elif field_name == 'gas_resistance':
                        # Gas resistance should be positive
                        # Allow up to 1e308 (near DOUBLE_MAX) for various sensors
                        if float_val <= 0 or float_val > 1e308:
                            return None
                    elif field_name == 'current':
                        # Current should be reasonable
                        # Allow wider range for various power monitoring setups
                        if not -50 <= float_val <= 50:
                            return None
                
                return float_val
            except (ValueError, TypeError):
                return None

        data = {
            "air_util_tx": None,
            "battery_level": None,
            "channel_utilization": None,
            "uptime_seconds": None,
            "voltage": None,
            "temperature": None,
            "relative_humidity": None,
            "barometric_pressure": None,
            "gas_resistance": None,
            "current": None,
            "channel": channel
        }

        metrics = [
            "device_metrics",
            "environment_metrics"
        ]
        for metric in metrics:
            if metric not in payload:
                continue
            for key in data:
                if key in payload[metric]:
                    data[key] = validate_telemetry_value(payload[metric][key], key)

        sql = """INSERT INTO telemetry
(id, air_util_tx, battery_level, channel_utilization,
uptime_seconds, voltage, temperature, relative_humidity,
barometric_pressure, gas_resistance, current, telemetry_time, channel)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FROM_UNIXTIME(%s), %s)
"""
        params = (
            node_id,
            data["air_util_tx"],
            data["battery_level"],
            data["channel_utilization"],
            data["uptime_seconds"],
            data["voltage"],
            data["temperature"],
            data["relative_humidity"],
            data["barometric_pressure"],
            data["gas_resistance"],
            data["current"],
            payload["time"],
            data["channel"]
        )
        self.db.cursor().execute(sql, params)
        self.db.commit()

    def store_text(self, data, topic):
        """Store text message."""
        if "from" not in data or "to" not in data or "decoded" not in data:
            return
        if "json_payload" not in data["decoded"] or "text" not in data["decoded"]["json_payload"]:
            return
                
        from_id = data["from"]
        to_id = data["to"]
        text = data["decoded"]["json_payload"]["text"]
        channel = data.get("channel", 0)
        message_id = data.get("id")

        if not message_id:  # Skip messages without ID
            return

        # Check if message already exists
        cur = self.db.cursor()
        cur.execute("SELECT 1 FROM text WHERE message_id = %s", (message_id,))
        exists = cur.fetchone()
        cur.close()

        if not exists:
            # Store the message only if it doesn't exist
            sql = """INSERT INTO text
            (from_id, to_id, text, channel, message_id, ts_created)
            VALUES (%s, %s, %s, %s, %s, NOW())"""
            params = (from_id, to_id, text, channel, message_id)
            cur = self.db.cursor()
            cur.execute(sql, params)
            cur.close()
            self.db.commit()

        # Reception information is now handled by the main store() method
        # No need to duplicate that code here
        
        # Check for meshinfo command
        if isinstance(text, bytes):
            text = text.decode()
        match = re.search(r"meshinfo (\d{4})", text, re.IGNORECASE)
        if match:
            otp = match.group(1)
            node = from_id
            self.claim_node(node, otp)

    def claim_node(self, node, otp):
        sql = """SELECT email FROM meshuser
WHERE otp = %s"""
        params = (otp, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        owner = row[0] if row else None
        cur.close()
        if not owner:
            return
        sql = """UPDATE meshuser
SET otp = NULL WHERE email = %s
"""
        params = (owner, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()

        sql = """UPDATE nodeinfo
SET owner = %s WHERE id = %s"""
        params = (
            owner,
            node
        )
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()

    def verify_node(self, id, via=None):
        query = "SELECT 1 FROM nodeinfo where id = %s"
        param = (id, )
        cur = self.db.cursor()
        cur.execute(query, param)
        found = True if cur.fetchone() else False
        cur.close()
        if not found:
            self.store_node(self.unknown(id))
        else:
            if via:
                # Only set ts_uplink if this node is reporting directly via MQTT (via is None or same as id)
                if via == id:
                    sql = """UPDATE nodeinfo SET
ts_seen = NOW(), updated_via = %s, ts_uplink = NOW() WHERE id = %s"""
                else:
                    sql = """UPDATE nodeinfo SET
ts_seen = NOW(), updated_via = %s WHERE id = %s"""
                param = (via, id)
            else:
                sql = "UPDATE nodeinfo SET ts_seen = NOW() WHERE id = %s"
                param = (id, )
            cur = self.db.cursor()
            cur.execute(sql, param)
            cur.close()
        return id

    def log_data(self, topic, data):
        cur = self.db.cursor()
        cur.execute(f"SELECT COUNT(*) FROM meshlog")
        count = cur.fetchone()[0]
        if count >= 1000:
            cur.execute(f"DELETE FROM meshlog ORDER BY ts_created ASC LIMIT 1")
        self.db.commit()

        sql = "INSERT INTO meshlog (topic, message) VALUES (%s, %s)"
        params = (topic, json.dumps(data, indent=4, cls=CustomJSONEncoder))
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()
        logging.debug(json.dumps(data, indent=4, cls=CustomJSONEncoder))

    def log_position(self, id, lat, lon, source):
        if not lat or not lon:
            return
        sql = """DELETE FROM positionlog
WHERE ts_created < NOW() - INTERVAL 1 DAY
AND id = %s"""
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()
        moved = True
        sql = """SELECT latitude_i, longitude_i FROM positionlog
WHERE id = %s ORDER BY ts_created DESC LIMIT 1"""
        params = (id, )
        cur = self.db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if row and row[0] == lat and row[1] == lon:
            moved = False
        cur.close()
        if not moved:
            return
        sql = """INSERT INTO positionlog
(id, latitude_i, longitude_i, source) VALUES (%s, %s, %s, %s)"""
        params = (id, lat, lon, source)
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()
        logging.info(f"Position updated for {id}")

    def store(self, data, topic):
        if not self.ping_db():
            logging.error("Database connection is not active. Skipping store operation.")
            return # Stop processing if DB is unavailable
        if not data:
            return
        self.log_data(topic, data)
        if "from" in data:
            frm = data["from"]
            via = self.int_id(topic.split("/")[-1])
            self.verify_node(frm, via)
        
        # Extract common hop information before processing specific message types
        message_id = data.get("id")
        hop_limit = data.get("hop_limit")
        hop_start = data.get("hop_start")
        rx_snr = data.get("rx_snr")
        rx_rssi = data.get("rx_rssi")
        
        # Store reception information if this is a received message with SNR/RSSI data
        if message_id and rx_snr is not None and rx_rssi is not None:
            received_by = None
            # Try to determine the receiving node from the topic
            topic_parts = topic.split("/")
            if len(topic_parts) > 1:
                received_by = self.int_id(topic_parts[-1])
                
            if received_by and received_by != data.get("from"):  # Don't store reception by sender
                self.store_reception(message_id, data["from"], received_by, rx_snr, rx_rssi, 
                                    data.get("rx_time"), hop_limit, hop_start)
        
        # Continue with the regular message type processing
        tp = data["type"]
        if tp == "nodeinfo":
            self.store_node(data)
        elif tp == "position":
            self.store_position(data)
        elif tp == "mapreport":
            self.store_mapreport(data)
        elif tp == "neighborinfo":
            self.store_neighborinfo(data)
        elif tp == "traceroute":
            self.store_traceroute(data)
        elif tp == "telemetry":
            self.store_telemetry(data)
        elif tp == "text":
            self.store_text(data, topic)  # Only one text handler, with topic parameter

    def store_reception(self, message_id, from_id, received_by_id, rx_snr, rx_rssi, rx_time, hop_limit, hop_start):
        """Store reception information for any message type with hop data."""
        sql = """INSERT INTO message_reception
        (message_id, from_id, received_by_id, rx_snr, rx_rssi, rx_time, hop_limit, hop_start)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        rx_snr = VALUES(rx_snr),
        rx_rssi = VALUES(rx_rssi),
        rx_time = VALUES(rx_time),
        hop_limit = VALUES(hop_limit),
        hop_start = VALUES(hop_start)"""
        params = (
            message_id,
            from_id,
            received_by_id,
            rx_snr,
            rx_rssi,
            rx_time,
            hop_limit,
            hop_start
        )
        cur = self.db.cursor()
        cur.execute(sql, params)
        cur.close()
        self.db.commit()

    def setup_database(self):
        creates = [
            """CREATE TABLE IF NOT EXISTS meshuser (
    email VARCHAR(255) PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    password BINARY(60) NOT NULL,
    verification CHAR(4),
    otp CHAR(4),
    status VARCHAR(12) DEFAULT 'CREATED',
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ts_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (username),
    INDEX idx_meshuser_username (username)
)""",
            """CREATE TABLE IF NOT EXISTS nodeinfo (
    id INT UNSIGNED PRIMARY KEY,
    long_name VARCHAR(40) NOT NULL,
    short_name VARCHAR(5) NOT NULL,
    hw_model INT UNSIGNED,
    role INT UNSIGNED,
    firmware_version VARCHAR(40),
    owner VARCHAR(255),
    updated_via INT UNSIGNED,
    ts_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ts_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner) REFERENCES meshuser(email)
)""",
            """CREATE TABLE IF NOT EXISTS position (
    id INT UNSIGNED PRIMARY KEY,
    altitude INT,
    ground_speed INT,
    ground_track INT,
    latitude_i INT,
    longitude_i INT,
    location_source INT,
    precision_bits INT UNSIGNED,
    position_time TIMESTAMP,
    geocoded VARCHAR(255),
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ts_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""",
            """CREATE TABLE IF NOT EXISTS neighborinfo (
    id INT UNSIGNED,
    neighbor_id INT UNSIGNED,
    snr INT SIGNED,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, neighbor_id)
)""",
            """CREATE TABLE IF NOT EXISTS traceroute (
    from_id INT UNSIGNED NOT NULL,
    to_id INT UNSIGNED NOT NULL,
    route VARCHAR(255),
    snr VARCHAR(255),
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""",
            """CREATE TABLE IF NOT EXISTS telemetry (
    id INT UNSIGNED NOT NULL,
    air_util_tx FLOAT(10, 7),
    battery_level INT,
    channel_utilization FLOAT(10, 7),
    uptime_seconds INT UNSIGNED,
    voltage FLOAT(10, 7),
    temperature FLOAT(10, 7),
    relative_humidity FLOAT(10, 7),
    barometric_pressure FLOAT(12, 7),
    gas_resistance DOUBLE,
    current FLOAT(10, 7),
    telemetry_time TIMESTAMP,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_telemetry_id (id)
)
""",
            """CREATE TABLE IF NOT EXISTS text (
    from_id INT UNSIGNED NOT NULL,
    to_id INT UNSIGNED NOT NULL,
    channel INT UNSIGNED NOT NULL,
    text VARCHAR(255),
    message_id BIGINT,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_text_message_id (message_id)
)""",
            """CREATE TABLE IF NOT EXISTS  meshlog (
    topic varchar(255) not null,
    message text,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""",
            """CREATE TABLE IF NOT EXISTS  positionlog (
    id INT UNSIGNED NOT NULL,
    latitude_i INT NOT NULL,
    longitude_i INT NOT NULL,
    source VARCHAR(35) NOT NULL,
    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, ts_created)
)"""
        ]
        cur = self.db.cursor()
        for create in creates:
            cur.execute(create)
        cur.close()

        # Run migrations before final commit
        try:
            # Use explicit path relative to meshdata.py location
            import os
            import sys
            migrations_path = os.path.join(os.path.dirname(__file__), 'migrations')
            sys.path.insert(0, os.path.dirname(__file__))
            from migrations import MIGRATIONS
            
            # Run all migrations in order
            for migration in MIGRATIONS:
                try:
                    migration(self.db)
                    logging.info(f"Successfully ran migration: {migration.__name__}")
                except Exception as e:
                    logging.error(f"Failed to run migration {migration.__name__}: {e}")
                    raise
                    
        except ImportError as e:
            logging.error(f"Failed to import migration module: {e}")
            # Continue with database setup even if migration fails
            pass
        except Exception as e:
            logging.error(f"Failed to run migrations: {e}")
            raise

        self.db.commit()

    def import_nodes(self, filename):
        fh = open(filename, "r")
        j = json.loads(fh.read())
        fh.close()
        records = []
        for node_id in j:
            record = {}
            node = j[node_id]
            int_id = self.int_id(node_id)
            record["id"] = int_id
            record["long_name"] = node["longname"]
            record["short_name"] = node["shortname"]
            record["hw_model"] = node["hardware"]
            record["role"] = node["role"] if "role" in node else 0
            if "mapreport" in node and "firmware_version" in node["mapreport"]:
                record["firmware_version"] = \
                    node["mapreport"]["firmware_version"]
            else:
                record["firmware_version"] = None
            records.append(record)

        for record in records:
            sql = """INSERT INTO nodeinfo (
    id,
    long_name,
    short_name,
    hw_model,
    role,
    firmware_version,
    ts_updated
)
VALUES (%s, %s, %s, %s, %s, %s, NOW())
ON DUPLICATE KEY UPDATE
long_name = VALUES(long_name),
short_name = VALUES(short_name),
hw_model = COALESCE(VALUES(hw_model), hw_model),
role = COALESCE(VALUES(role), role),
firmware_version = COALESCE(VALUES(firmware_version), firmware_version),
ts_updated = VALUES(ts_updated)"""
            values = (
                record["id"],
                record["long_name"],
                record["short_name"],
                record["hw_model"],
                record["role"],
                record["firmware_version"]
            )
            cur = self.db.cursor()
            cur.execute(sql, values)
        self.db.commit()

    def import_chat(self, filename):
        fh = open(filename, "r")
        j = json.loads(fh.read())
        fh.close()
        records = []
        for channel in j["channels"]:
            for message in j["channels"][channel]["messages"]:
                records.append({
                    "from_id": self.int_id(message["from"]),
                    "to_id": self.int_id(message["to"]),
                    "channel": channel,
                    "text": message["text"],
                    "ts_created": message["timestamp"]
                })
        sorted_records = sorted(
            records,
            key=lambda x: x["ts_created"],
            reverse=True
        )
        sql = """INSERT into text (from_id, to_id, channel, text, ts_created)
VALUES (%s, %s, %s, %s, FROM_UNIXTIME(%s))
"""
        total = len(sorted_records)
        count = 1
        for record in sorted_records:
            params = (
                record["from_id"],
                record["to_id"],
                record["channel"],
                record["text"],
                record["ts_created"]
            )
            print(f"Writing record {count} of {total} ...")
            count += 1
            try:
                cur = self.db.cursor()
                cur.execute(sql, params)
                cur.close()
            except Exception as e:
                print(f"failed to write record.")
        self.db.commit()

    def get_neighbor_info_links(self, days=1):
        """
        Fetch neighbor info links from the database.
        
        Args:
            days: Number of days to look back for neighbor info data
            
        Returns:
            Dictionary with node_id_int as keys and neighbor info data as values
        """
        neighbor_info_links = {}  # {node_id_int: {'heard': {neighbor_id_int: {data}}, ...}}
        
        cursor = self.db.cursor(dictionary=True)
        # Fetch links from the specified days, adjust interval as needed
        cursor.execute("""
            SELECT
                ni.id, ni.neighbor_id, ni.snr,
                p1.latitude_i as lat1_i, p1.longitude_i as lon1_i,
                p2.latitude_i as lat2_i, p2.longitude_i as lon2_i
            FROM neighborinfo ni
            LEFT OUTER JOIN position p1 ON p1.id = ni.id
            LEFT OUTER JOIN position p2 ON p2.id = ni.neighbor_id
            WHERE ni.ts_created >= NOW() - INTERVAL %s DAY
        """, (days,))
        
        for row in cursor.fetchall():
            node_id_int = row['id']
            neighbor_id_int = row['neighbor_id']
            distance = None
            if (row['lat1_i'] and row['lon1_i'] and row['lat2_i'] and row['lon2_i']):
                distance = round(utils.distance_between_two_points(
                    row['lat1_i'] / 10000000, row['lon1_i'] / 10000000,
                    row['lat2_i'] / 10000000, row['lon2_i'] / 10000000
                ), 2)

            link_data = {
                'snr': row['snr'], 
                'distance': distance, 
                'neighbor_id': neighbor_id_int, 
                'source_type': 'neighbor_info'
            }

            if node_id_int not in neighbor_info_links:
                neighbor_info_links[node_id_int] = {'heard': {}, 'heard_by': {}}
            
            # Add to node_id's 'heard' list
            neighbor_info_links[node_id_int]['heard'][neighbor_id_int] = link_data
            
            # Add to neighbor_id's 'heard_by' list
            if neighbor_id_int not in neighbor_info_links:
                neighbor_info_links[neighbor_id_int] = {'heard': {}, 'heard_by': {}}
            
            heard_by_data = {
                'snr': row['snr'], 
                'distance': distance, 
                'neighbor_id': node_id_int,
                'source_type': 'neighbor_info'
            }
            neighbor_info_links[neighbor_id_int]['heard_by'][node_id_int] = heard_by_data

        cursor.close()
        return neighbor_info_links

    def get_zero_hop_links(self, cutoff_time):
        """
        Fetch zero-hop links from the database.
        
        Args:
            cutoff_time: Unix timestamp for the cutoff time
            
        Returns:
            Dictionary with node_id_int as keys and zero-hop data as values
        """
        zero_hop_links = {}  # {node_id_int: {'heard': {neighbor_id_int: {data}}, 'heard_by': {neighbor_id_int: {data}}}}
        zero_hop_last_heard = {}  # Keep track of last heard time for sorting
        
        cursor = self.db.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                m.from_id,
                m.received_by_id,
                MAX(m.rx_snr) as snr,
                COUNT(*) as message_count,
                MAX(m.rx_time) as last_heard_time,
                p_sender.latitude_i as lat_sender_i,
                p_sender.longitude_i as lon_sender_i,
                p_receiver.latitude_i as lat_receiver_i,
                p_receiver.longitude_i as lon_receiver_i
            FROM message_reception m
            LEFT OUTER JOIN position p_sender ON p_sender.id = m.from_id
            LEFT OUTER JOIN position p_receiver ON p_receiver.id = m.received_by_id
            WHERE m.rx_time > %s
            AND (
                (m.hop_limit IS NULL AND m.hop_start IS NULL)
                OR
                (m.hop_start - m.hop_limit = 0)
            )
            GROUP BY m.from_id, m.received_by_id,
                     p_sender.latitude_i, p_sender.longitude_i,
                     p_receiver.latitude_i, p_receiver.longitude_i
        """, (cutoff_time,))

        for row in cursor.fetchall():
            sender_id = row['from_id']
            receiver_id = row['received_by_id']
            last_heard_dt = datetime.datetime.fromtimestamp(row['last_heard_time'])

            # Update last heard time for involved nodes
            zero_hop_last_heard[sender_id] = max(zero_hop_last_heard.get(sender_id, datetime.datetime.min), last_heard_dt)
            zero_hop_last_heard[receiver_id] = max(zero_hop_last_heard.get(receiver_id, datetime.datetime.min), last_heard_dt)

            distance = None
            if (row['lat_sender_i'] and row['lon_sender_i'] and
                row['lat_receiver_i'] and row['lon_receiver_i']):
                distance = round(utils.distance_between_two_points(
                    row['lat_sender_i'] / 10000000, row['lon_sender_i'] / 10000000,
                    row['lat_receiver_i'] / 10000000, row['lon_receiver_i'] / 10000000
                ), 2)

            link_data = {
                'snr': row['snr'],
                'message_count': row['message_count'],
                'distance': distance,
                'last_heard': last_heard_dt,
                'neighbor_id': sender_id,  # For receiver, neighbor is sender
                'source_type': 'zero_hop'
            }
            
            heard_by_data = {
                'snr': row['snr'],
                'message_count': row['message_count'],
                'distance': distance,
                'last_heard': last_heard_dt,
                'neighbor_id': receiver_id,  # For sender, neighbor is receiver
                'source_type': 'zero_hop'
            }

            # Add to receiver's 'heard' list
            if receiver_id not in zero_hop_links:
                zero_hop_links[receiver_id] = {'heard': {}, 'heard_by': {}}
            zero_hop_links[receiver_id]['heard'][sender_id] = link_data

            # Add to sender's 'heard_by' list
            if sender_id not in zero_hop_links:
                zero_hop_links[sender_id] = {'heard': {}, 'heard_by': {}}
            zero_hop_links[sender_id]['heard_by'][receiver_id] = heard_by_data

        cursor.close()
        return zero_hop_links, zero_hop_last_heard

    def get_graph_data(self, view_type='merged', days=1, zero_hop_timeout=43200):
        """
        Get graph data for visualization.
        
        Args:
            view_type: 'neighbor_info', 'zero_hop', or 'merged'
            days: Number of days to look back for neighbor info data
            zero_hop_timeout: Timeout in seconds for zero-hop data
            
        Returns:
            Dictionary with nodes and edges for graph visualization
        """
        nodes = self.get_nodes()
        nodes_for_graph = []
        edges_for_graph = []
        active_node_ids_hex = set()  # Keep track of nodes to include in the graph
        
        # Get neighbor info links
        neighbor_info_links = {}
        if view_type in ['neighbor_info', 'merged']:
            neighbor_info_links = self.get_neighbor_info_links(days)
            
            # Add involved nodes to the active set if they exist in our main nodes list
            for node_id_int, links in neighbor_info_links.items():
                node_id_hex = utils.convert_node_id_from_int_to_hex(node_id_int)
                if node_id_hex in nodes and nodes[node_id_hex].get("active"):
                    active_node_ids_hex.add(node_id_hex)
                
                for neighbor_id_int in links.get('heard', {}):
                    neighbor_id_hex = utils.convert_node_id_from_int_to_hex(neighbor_id_int)
                    if neighbor_id_hex in nodes and nodes[neighbor_id_hex].get("active"):
                        active_node_ids_hex.add(neighbor_id_hex)
        
        # Get zero-hop links
        zero_hop_links = {}
        if view_type in ['zero_hop', 'merged']:
            cutoff_time = int(time.time()) - zero_hop_timeout
            zero_hop_links, _ = self.get_zero_hop_links(cutoff_time)
            
            # Add involved nodes to the active set if they exist
            for node_id_int, links in zero_hop_links.items():
                node_id_hex = utils.convert_node_id_from_int_to_hex(node_id_int)
                if node_id_hex in nodes and nodes[node_id_hex].get("active"):
                    active_node_ids_hex.add(node_id_hex)
                
                for neighbor_id_int in links.get('heard', {}):
                    neighbor_id_hex = utils.convert_node_id_from_int_to_hex(neighbor_id_int)
                    if neighbor_id_hex in nodes and nodes[neighbor_id_hex].get("active"):
                        active_node_ids_hex.add(neighbor_id_hex)
        
        # Build nodes for graph
        for node_id_hex in active_node_ids_hex:
            node_data = nodes[node_id_hex]
            
            # Get HW Model Name safely
            hw_model = node_data.get('hw_model')
            hw_model_name = get_hardware_model_name(hw_model)
            
            # Get Icon URL
            node_name_for_icon = node_data.get('long_name', node_data.get('short_name', ''))
            icon_url = utils.graph_icon(node_name_for_icon)
            
            nodes_for_graph.append({
                'id': node_id_hex,
                'short': node_data.get('short_name', 'UNK'),
                'icon_url': icon_url,
                'node_data': {
                    'long_name': node_data.get('long_name', 'Unknown Name'),
                    'hw_model': hw_model_name,
                    'last_seen': time_ago(node_data.get('ts_seen')) if node_data.get('ts_seen') else 'Never'
                }
            })
        
        # Build edges for graph
        added_node_pairs = set()
        
        # Add Neighbor Info Edges
        if view_type in ['neighbor_info', 'merged']:
            for node_id_int, links in neighbor_info_links.items():
                for neighbor_id_int, data in links.get('heard', {}).items():
                    from_node_hex = utils.convert_node_id_from_int_to_hex(node_id_int)
                    to_node_hex = utils.convert_node_id_from_int_to_hex(neighbor_id_int)
                    
                    # Ensure both nodes are active and in our graph node list
                    if from_node_hex in active_node_ids_hex and to_node_hex in active_node_ids_hex:
                        node_pair = tuple(sorted((from_node_hex, to_node_hex)))
                        # Add edge only if this node pair hasn't been added yet
                        if node_pair not in added_node_pairs:
                            edges_for_graph.append({
                                'from': from_node_hex,
                                'to': to_node_hex,
                                'edge_data': data  # Contains snr, distance, source_type='neighbor_info'
                            })
                            added_node_pairs.add(node_pair)  # Mark pair as added
        
        # Add Zero Hop Edges (only if not already added via Neighbor Info)
        if view_type in ['zero_hop', 'merged']:
            for receiver_id_int, links in zero_hop_links.items():
                for sender_id_int, data in links.get('heard', {}).items():
                    # For zero hop, 'from' is sender, 'to' is receiver
                    from_node_hex = utils.convert_node_id_from_int_to_hex(sender_id_int)
                    to_node_hex = utils.convert_node_id_from_int_to_hex(receiver_id_int)
                    
                    # Ensure both nodes are active and in our graph node list
                    if from_node_hex in active_node_ids_hex and to_node_hex in active_node_ids_hex:
                        node_pair = tuple(sorted((from_node_hex, to_node_hex)))
                        # Add edge only if this node pair hasn't been added yet
                        if node_pair not in added_node_pairs:
                            edges_for_graph.append({
                                'from': from_node_hex,
                                'to': to_node_hex,
                                'edge_data': data  # Contains snr, distance, source_type='zero_hop'
                            })
                            added_node_pairs.add(node_pair)  # Mark pair as added
        
        # Combine nodes and edges
        graph_data = {
            'nodes': nodes_for_graph,
            'edges': edges_for_graph
        }
        
        return graph_data

    def get_neighbors_data(self, view_type='neighbor_info', days=1, zero_hop_timeout=43200):
        """
        Get neighbors data for the neighbors page.
        
        Args:
            view_type: 'neighbor_info', 'zero_hop', or 'merged'
            days: Number of days to look back for neighbor info data
            zero_hop_timeout: Timeout in seconds for zero-hop data
            
        Returns:
            Dictionary with node_id_hex as keys and neighbor data as values
        """
        nodes = self.get_nodes()
        if not nodes:
            return {}
            
        # Get neighbor info links
        neighbor_info_links = {}
        if view_type in ['neighbor_info', 'merged']:
            neighbor_info_links = self.get_neighbor_info_links(days)
        
        # Get zero-hop links
        zero_hop_links = {}
        zero_hop_last_heard = {}
        if view_type in ['zero_hop', 'merged']:
            cutoff_time = int(time.time()) - zero_hop_timeout
            zero_hop_links, zero_hop_last_heard = self.get_zero_hop_links(cutoff_time)
        
        # Dictionary to hold the final data for active nodes
        active_nodes_data = {}
        
        # Combine data for active nodes based on view type
        for node_id_hex, node_base_data in nodes.items():
            if not node_base_data.get("active"):
                continue  # Skip inactive nodes
                
            node_id_int = utils.convert_node_id_from_hex_to_int(node_id_hex)
            final_node_data = dict(node_base_data)  # Start with base data
            
            # Initialize lists
            final_node_data['neighbors'] = []
            final_node_data['heard_by_neighbors'] = []
            final_node_data['zero_hop_neighbors'] = []
            final_node_data['heard_by_zero_hop'] = []
            
            has_neighbor_info = node_id_int in neighbor_info_links
            has_zero_hop_info = node_id_int in zero_hop_links
            
            # Determine overall last heard time for sorting
            last_heard_zero_hop = max([d['last_heard'] for d in zero_hop_links[node_id_int]['heard'].values()], default=datetime.datetime.min) if has_zero_hop_info else datetime.datetime.min
            last_heard_by_zero_hop = max([d['last_heard'] for d in zero_hop_links[node_id_int]['heard_by'].values()], default=datetime.datetime.min) if has_zero_hop_info else datetime.datetime.min
            
            node_ts_seen = datetime.datetime.fromtimestamp(node_base_data['ts_seen']) if node_base_data.get('ts_seen') else datetime.datetime.min
            
            final_node_data['last_heard'] = max(
                node_ts_seen,
                zero_hop_last_heard.get(node_id_int, datetime.datetime.min)  # Use precalculated zero hop time
                # We don't need to include neighbor info times here as they aren't distinct per-link
            )
            
            include_node = False
            if view_type in ['neighbor_info', 'merged']:
                if has_neighbor_info:
                    final_node_data['neighbors'] = list(neighbor_info_links[node_id_int]['heard'].values())
                    final_node_data['heard_by_neighbors'] = list(neighbor_info_links[node_id_int]['heard_by'].values())
                    if final_node_data['neighbors'] or final_node_data['heard_by_neighbors']:
                        include_node = True
            
            if view_type in ['zero_hop', 'merged']:
                if has_zero_hop_info:
                    final_node_data['zero_hop_neighbors'] = list(zero_hop_links[node_id_int]['heard'].values())
                    final_node_data['heard_by_zero_hop'] = list(zero_hop_links[node_id_int]['heard_by'].values())
                    if final_node_data['zero_hop_neighbors'] or final_node_data['heard_by_zero_hop']:
                        include_node = True
            
            if include_node:
                active_nodes_data[node_id_hex] = final_node_data
        
        # Sort final results by last heard time
        active_nodes_data = dict(sorted(
            active_nodes_data.items(),
            key=lambda item: item[1].get('last_heard', datetime.datetime.min),
            reverse=True
        ))
        
        return active_nodes_data

    def is_position_fresh(self, position, prune_threshold, now=None):
        """
        Returns True if the position dict/object has a position_time within prune_threshold seconds of now.
        """
        if not position:
            return False
        # Accept both dict and object
        position_time = None
        if isinstance(position, dict):
            position_time = position.get('position_time')
        else:
            position_time = getattr(position, 'position_time', None)
        if not position_time:
            return False
        if isinstance(position_time, datetime.datetime):
            position_time = position_time.timestamp()
        if now is None:
            now = time.time()
        return (now - position_time) <= prune_threshold

    def get_telemetry_for_node(self, node_id):
        """
        Return the last 24 hours of telemetry for the given node, ordered by timestamp ascending.
        Each record is a dict with keys: air_util_tx, battery_level, channel_utilization, ts_created, etc.
        """
        telemetry = []
        sql = """
            SELECT
                DATE_FORMAT(ts_created, '%Y-%m-%d %H:%i:00') as interval_start,
                FLOOR(MINUTE(ts_created) / 10) as ten_minute_block,
                AVG(air_util_tx) as air_util_tx,
                AVG(battery_level) as battery_level,
                AVG(channel_utilization) as channel_utilization,
                UNIX_TIMESTAMP(MIN(ts_created)) as ts_created
            FROM telemetry
            WHERE id = %s AND ts_created >= NOW() - INTERVAL 1 DAY
            GROUP BY interval_start
            ORDER BY interval_start ASC
        """
        params = (node_id,)
        cur = self.db.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        column_names = [desc[0] for desc in cur.description]
        for row in rows:
            record = {}
            for i in range(len(row)):
                value = row[i]
                if isinstance(value, datetime.datetime):
                    record[column_names[i]] = int(value.timestamp())
                else:
                    record[column_names[i]] = value
            telemetry.append(record)
        cur.close()
        return telemetry

    def get_positions_at_time(self, node_ids, timestamp):
        """Get the closest position for each node using a single reused cursor."""
        if not node_ids:
            return {}
        results = {}
        cur = self.db.cursor(dictionary=True)
        for node_id in node_ids:
            pos = self.get_position_at_time(node_id, timestamp, cur)
            if pos:
                results[node_id] = pos
        cur.close()
        return results

    def get_position_at_time(self, node_id, target_timestamp, cur=None):
        """Retrieves the position record from positionlog for a node that is closest to, but not after, the target timestamp."""
        position = {}
        close_cur = False
        if cur is None:
            cur = self.db.cursor(dictionary=True)
            close_cur = True
        try:
            target_dt = datetime.datetime.fromtimestamp(target_timestamp)
            sql = """SELECT latitude_i, longitude_i, ts_created
                     FROM positionlog
                     WHERE id = %s
                     ORDER BY ABS(TIMESTAMPDIFF(SECOND, ts_created, %s)) ASC
                     LIMIT 1"""
            params = (node_id, target_dt)
            cur.execute(sql, params)
            row = cur.fetchone()
            if row:
                position = {
                    "latitude_i": row["latitude_i"],
                    "longitude_i": row["longitude_i"],
                    "position_time": row["ts_created"].timestamp() if isinstance(row["ts_created"], datetime.datetime) else row["ts_created"],
                    "latitude": row["latitude_i"] / 10000000 if row["latitude_i"] else None,
                    "longitude": row["longitude_i"] / 10000000 if row["longitude_i"] else None
                }
        except mysql.connector.Error as err:
            logging.error(f"Database error fetching nearest position for {node_id}: {err}")
        except Exception as e:
            logging.error(f"Error fetching nearest position for {node_id}: {e}")
        finally:
            if close_cur:
                cur.close()
        return position

    def get_reception_details_batch(self, message_id, receiver_ids):
        """Get reception details for multiple receivers in one query"""
        if not receiver_ids:
            return {}
        
        # Handle single receiver case
        if len(receiver_ids) == 1:
            query = """
                SELECT received_by_id, rx_snr, rx_rssi, rx_time, hop_limit, hop_start
                FROM message_reception
                WHERE message_id = %s AND received_by_id = %s
            """
            params = (message_id, receiver_ids[0])
        else:
            placeholders = ','.join(['%s'] * len(receiver_ids))
            query = f"""
                SELECT received_by_id, rx_snr, rx_rssi, rx_time, hop_limit, hop_start
                FROM message_reception
                WHERE message_id = %s AND received_by_id IN ({placeholders})
            """
            params = (message_id, *receiver_ids)

        cur = None
        try:
            cur = self.db.cursor(dictionary=True)
            cur.execute(query, params)
            return {row['received_by_id']: row for row in cur.fetchall()}
        except Exception as e:
            logging.error(f"Error fetching reception details: {e}")
            return {}
        finally:
            if cur:
                cur.close()


def create_database():
    config = configparser.ConfigParser()
    config.read('config.ini')

    db = mysql.connector.connect(
        host="db",
        user="root",
        password="passw0rd",
    )
    sqls = [
        f"""CREATE DATABASE IF NOT EXISTS {config["database"]["database"]}""",
        f"""CREATE USER IF NOT EXISTS '{config["database"]["username"]}'@'%'
IDENTIFIED BY '{config["database"]["password"]}'""",
        f"""GRANT ALL ON {config["database"]["username"]}.*
TO '$DB_USER'@'%'""",
        f"""ALTER DATABASE {config["database"]["database"]}
CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
    ]
    for sql in sqls:
        cur = db.cursor()
        cur.execute(sql)
        cur.close()
    db.commit()


if __name__ == "__main__":
    md = MeshData()
    md.setup_database()
