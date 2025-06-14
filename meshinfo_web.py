from flask import (
    Flask,
    send_from_directory,
    render_template,
    request,
    make_response,
    redirect,
    url_for,
    abort,
    g,
    jsonify,
    current_app
)
from flask_caching import Cache
from waitress import serve
from paste.translogger import TransLogger
import configparser
import logging
import os
import psutil
import gc
import weakref
import threading
import time
import re
import sys
import math
from shapely.geometry import MultiPoint

import utils
import meshtastic_support
from meshdata import MeshData
from meshinfo_register import Register
from meshtastic_monday import MeshtasticMonday
from meshinfo_telemetry_graph import draw_graph
from meshinfo_los_profile import LOSProfile
from timezone_utils import convert_to_local, format_timestamp, time_ago
import json
import datetime

app = Flask(__name__)

cache_dir = os.path.join(os.path.dirname(__file__), 'runtime_cache')

# Ensure the cache directory exists
if not os.path.exists(cache_dir):
    try:
        os.makedirs(cache_dir)
        logging.info(f"Created cache directory: {cache_dir}")
    except OSError as e:
        logging.error(f"Could not create cache directory {cache_dir}: {e}")
        cache_dir = None # Indicate failure

# Configure Flask-Caching
cache_config = {
    'CACHE_TYPE': 'FileSystemCache',
    'CACHE_DIR': cache_dir,
    'CACHE_THRESHOLD': 500,
    'CACHE_DEFAULT_TIMEOUT': 60,
    'CACHE_OPTIONS': {
        'mode': 0o600
    }
}
if cache_dir:
    logging.info(f"Using FileSystemCache with directory: {cache_dir}")
else:
    logging.warning("Falling back to SimpleCache due to directory creation issues.")

# Initialize Cache with the chosen config
try:
    cache = Cache(app, config=cache_config)
except Exception as e:
    logging.error(f"Failed to initialize cache: {e}")
    # Fallback to SimpleCache if FileSystemCache fails
    cache_config = {
        'CACHE_TYPE': 'SimpleCache',
        'CACHE_DEFAULT_TIMEOUT': 300,
        'CACHE_THRESHOLD': 500
    }
    cache = Cache(app, config=cache_config)

# Make globals available to templates
app.jinja_env.globals.update(convert_to_local=convert_to_local)
app.jinja_env.globals.update(format_timestamp=format_timestamp)
app.jinja_env.globals.update(time_ago=time_ago)
app.jinja_env.globals.update(min=min)
app.jinja_env.globals.update(max=max)

# Add template filters
@app.template_filter('safe_hw_model')
def safe_hw_model(value):
    try:
        return meshtastic_support.get_hardware_model_name(value)
    except (ValueError, AttributeError):
        return f"Unknown ({value})"

config = configparser.ConfigParser()
config.read("config.ini")

# Add request context tracking
active_requests = set()
request_lock = threading.Lock()

# Add memory usage tracking
last_memory_log = 0
last_memory_usage = 0
MEMORY_LOG_INTERVAL = 300  # Log every 5 minutes
MEMORY_CHANGE_THRESHOLD = 50 * 1024 * 1024  # 50MB change threshold

@app.before_request
def before_request():
    """Track request start."""
    with request_lock:
        active_requests.add(id(request))
    # log_memory_usage()  # Removed per-request memory logging

@app.after_request
def after_request(response):
    """Clean up request context."""
    with request_lock:
        active_requests.discard(id(request))
    # log_memory_usage()  # Removed per-request memory logging
    return response

# Modify get_meshdata to use connection pooling
def get_meshdata():
    """Opens a new MeshData connection if there is none yet for the
    current application context.
    """
    if 'meshdata' not in g:
        try:
            # Create new MeshData instance without connection pooling
            g.meshdata = MeshData()
            logging.debug("MeshData instance created for request context.")
        except Exception as e:
            logging.error(f"Failed to create MeshData for request context: {e}")
            g.meshdata = None
    return g.meshdata

@app.teardown_appcontext
def teardown_meshdata(exception):
    """Closes the MeshData connection at the end of the request."""
    md = g.pop('meshdata', None)
    if md is not None:
        try:
            if hasattr(md, 'db') and md.db and md.db.is_connected():
                md.db.close()
                logging.debug("Database connection closed in teardown.")
        except Exception as e:
            logging.error(f"Error handling database connection in teardown: {e}")
        logging.debug("MeshData instance removed from request context.")

def log_memory_usage(force=False):
    """Log current memory usage with detailed information."""
    global last_memory_log, last_memory_usage
    current_time = time.time()
    current_usage = psutil.Process().memory_info().rss
    
    # Only log if:
    # 1. It's been more than MEMORY_LOG_INTERVAL seconds since last log
    # 2. Memory usage has changed by more than threshold
    # 3. Force flag is set
    if not force and (current_time - last_memory_log < MEMORY_LOG_INTERVAL and 
                     abs(current_usage - last_memory_usage) < MEMORY_CHANGE_THRESHOLD):
        return
        
    try:
        import gc
        gc.collect()  # Force garbage collection before measuring
        
        process = psutil.Process()
        mem_info = process.memory_info()
        
        # Get memory usage by object type
        objects_by_type = {}
        for obj in gc.get_objects():
            obj_type = type(obj).__name__
            if obj_type not in objects_by_type:
                objects_by_type[obj_type] = 0
            try:
                objects_by_type[obj_type] += sys.getsizeof(obj)
            except:
                pass
        
        # Sort object types by memory usage
        sorted_objects = sorted(objects_by_type.items(), key=lambda x: x[1], reverse=True)
        
        logging.info(f"Memory Usage: {mem_info.rss / 1024 / 1024:.1f} MB")
        logging.info(f"Active Requests: {len(active_requests)}")
        logging.info("Top 5 memory-consuming object types:")
        for obj_type, size in sorted_objects[:5]:
            logging.info(f"  {obj_type}: {size / 1024 / 1024:.1f} MB")
            
        last_memory_log = current_time
        last_memory_usage = current_usage
        
    except Exception as e:
        logging.error(f"Error in detailed memory logging: {e}")

# Add connection monitoring
def monitor_connections():
    """Monitor database connections."""
    while True:
        try:
            with app.app_context():
                if hasattr(g, 'meshdata') and g.meshdata and hasattr(g.meshdata, 'db'):
                    if g.meshdata.db.is_connected():
                        logging.info("Database connection is active")
                    else:
                        logging.warning("Database connection is not active")
        except Exception as e:
            logging.error(f"Error monitoring database connection: {e}")
        time.sleep(60)  # Check every minute

# Add cache lock monitoring
def monitor_cache_locks():
    """Monitor cache lock files."""
    while True:
        try:
            if cache_dir:
                lock_files = [f for f in os.listdir(cache_dir) if f.endswith('.lock')]
                if lock_files:
                    logging.warning(f"Found {len(lock_files)} stale cache locks")
                    # Clean up stale locks
                    for lock_file in lock_files:
                        try:
                            os.remove(os.path.join(cache_dir, lock_file))
                        except Exception as e:
                            logging.error(f"Error removing stale lock {lock_file}: {e}")
        except Exception as e:
            logging.error(f"Error monitoring cache locks: {e}")
        time.sleep(300)  # Check every 5 minutes

# Add memory watchdog
def memory_watchdog():
    """Monitor memory usage and take action if it gets too high."""
    while True:
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            if memory_mb > 2000:  # If over 2GB
                logging.warning(f"Memory usage high ({memory_mb:.2f} MB), clearing cache and forcing GC")
                with app.app_context():
                    cache.clear()
                gc.collect()
                
            if memory_mb > 4000:  # If over 4GB
                logging.error(f"Memory usage critical ({memory_mb:.2f} MB), logging detailed memory info")
                log_memory_usage()
                
        except Exception as e:
            logging.error(f"Error in memory watchdog: {e}")
            
        time.sleep(60)  # Check every minute

# Start monitoring threads
connection_monitor_thread = threading.Thread(target=monitor_connections, daemon=True)
connection_monitor_thread.start()

lock_monitor_thread = threading.Thread(target=monitor_cache_locks, daemon=True)
lock_monitor_thread.start()

watchdog_thread = threading.Thread(target=memory_watchdog, daemon=True)
watchdog_thread.start()

# Schedule cache cleanup
def cleanup_cache():
    try:
        logging.info("Starting cache cleanup")
        logging.info("Memory usage before cache cleanup:")
        log_memory_usage()
        
        # Clear the cache
        with app.app_context():
            cache.clear()
        
        # Force garbage collection
        gc.collect()
        
        logging.info("Memory usage after cache cleanup:")
        log_memory_usage()
        
    except Exception as e:
        logging.error(f"Error during cache cleanup: {e}")

# Schedule more frequent cache cleanup
def schedule_cache_cleanup():
    while True:
        time.sleep(900)  # Run every 15 minutes instead of hourly
        cleanup_cache()

cleanup_thread = threading.Thread(target=schedule_cache_cleanup, daemon=True)
cleanup_thread.start()

def auth():
    jwt = request.cookies.get('jwt')
    if not jwt:
        return None
    reg = Register()
    decoded_jwt = reg.auth(jwt)
    return decoded_jwt


@app.errorhandler(404)
def not_found(e):
    return render_template(
        "404.html.j2",
        auth=auth,
        config=config
    ), 404


# Serve static files from the root directory
@app.route('/')
@cache.cached(timeout=60)  # Cache for 60 seconds
def serve_index(success_message=None, error_message=None):
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    return render_template(
        "index.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        active_nodes=utils.active_nodes(nodes),
        timestamp=datetime.datetime.now(),
        success_message=success_message,
        error_message=error_message
    )


@app.route('/nodes.html')
@cache.cached(timeout=60)  # Cache for 60 seconds
def nodes():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    logging.info(f"/nodes.html: Loaded {len(nodes)} nodes.") # Add this
    latest = md.get_latest_node()
    return render_template(
        "nodes.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        show_inactive=False,  # Add this line
        latest=latest,
        hardware=meshtastic_support.HardwareModel,
        meshtastic_support=meshtastic_support,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/allnodes.html')
@cache.cached(timeout=60)  # Cache for 60 seconds
def allnodes():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    latest = md.get_latest_node()
    return render_template(
        "nodes.html.j2",  # Change to use nodes.html.j2
        auth=auth(),
        config=config,
        nodes=nodes,
        show_inactive=True,
        latest=latest,
        hardware=meshtastic_support.HardwareModel,
        meshtastic_support=meshtastic_support,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/chat-classic.html')
def chat():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    chat_data = md.get_chat(page=page, per_page=per_page)
    
    # start_item and end_item for pagination
    chat_data['start_item'] = (page - 1) * per_page + 1 if chat_data['total'] > 0 else 0
    chat_data['end_item'] = min(page * per_page, chat_data['total'])
    
    return render_template(
        "chat.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        chat=chat_data["items"],
        pagination=chat_data,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        debug=False,
    )

@app.route('/chat.html')
def chat2():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    chat_data = md.get_chat(page=page, per_page=per_page)
    
    chat_data['start_item'] = (page - 1) * per_page + 1 if chat_data['total'] > 0 else 0
    chat_data['end_item'] = min(page * per_page, chat_data['total'])
    
    return render_template(
        "chat2.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        chat=chat_data["items"],
        pagination=chat_data,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        meshtastic_support=meshtastic_support,
        debug=False,
    )

@app.route('/message_map.html')
@cache.cached(timeout=60, query_string=True)  # Cache for 60 seconds, vary by query string
def message_map():
    message_id = request.args.get('id')
    if not message_id:
        return redirect(url_for('chat'))
        
    meshdata = get_meshdata()
    if not meshdata:
        abort(503, description="Database connection unavailable")

    # Get current node info for names
    nodes = meshdata.get_nodes()
    
    # Get message and basic reception data
    cursor = meshdata.db.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.*, GROUP_CONCAT(r.received_by_id) as receiver_ids
        FROM text t
        LEFT JOIN message_reception r ON t.message_id = r.message_id
        WHERE t.message_id = %s
        GROUP BY t.message_id
    """, (message_id,))

    message_base = cursor.fetchone()
    cursor.close()

    if not message_base:
        return redirect(url_for('chat'))

    # Get the precise message time
    message_time = message_base['ts_created'].timestamp()

    # Batch load all positions at once
    receiver_ids_list = [int(r_id) for r_id in message_base['receiver_ids'].split(',')] if message_base['receiver_ids'] else []
    node_ids = [message_base['from_id']] + receiver_ids_list
    positions = meshdata.get_positions_at_time(node_ids, message_time)

    # Fallback: If sender position is missing, fetch it directly
    if message_base['from_id'] not in positions:
        sender_fallback = meshdata.get_position_at_time(message_base['from_id'], message_time)
        if sender_fallback:
            positions[message_base['from_id']] = sender_fallback

    # Batch load all reception details
    reception_details = meshdata.get_reception_details_batch(message_id, receiver_ids_list)
    
    # Ensure keys are int for lookups
    receiver_positions = {int(k): v for k, v in positions.items() if k in receiver_ids_list}
    receiver_details = {int(k): v for k, v in reception_details.items() if k in receiver_ids_list}
    sender_position = positions.get(message_base['from_id'])

    # Calculate convex hull area in square km
    points = []
    if sender_position and sender_position['latitude'] is not None and sender_position['longitude'] is not None:
        points.append((sender_position['longitude'], sender_position['latitude']))
    for pos in receiver_positions.values():
        if pos and pos['latitude'] is not None and pos['longitude'] is not None:
            points.append((pos['longitude'], pos['latitude']))
    convex_hull_area_km2 = None
    if len(points) >= 3:
        # Use shapely to calculate convex hull area
        hull = MultiPoint(points).convex_hull
        # Approximate area on Earth's surface (convert degrees to meters using haversine formula)
        # We'll use a simple equirectangular projection for small areas
        # Reference point for projection
        avg_lat = sum(lat for lon, lat in points) / len(points)
        earth_radius = 6371.0088  # km
        def latlon_to_xy(lon, lat):
            x = math.radians(lon) * earth_radius * math.cos(math.radians(avg_lat))
            y = math.radians(lat) * earth_radius
            return (x, y)
        xy_points = [latlon_to_xy(lon, lat) for lon, lat in hull.exterior.coords]
        hull_xy = MultiPoint(xy_points).convex_hull
        convex_hull_area_km2 = hull_xy.area
    
    # Prepare message object for template
    message = {
        'id': message_id,
        'from_id': message_base['from_id'],
        'text': message_base['text'],
        'ts_created': message_time,
        'receiver_ids': receiver_ids_list
    }

    # Info-level logging for debugging
    logging.info(f"[message_map] Receiver IDs: {receiver_ids_list}")
    logging.info(f"[message_map] Receiver Positions Keys: {list(receiver_positions.keys())}")
    logging.info(f"[message_map] Receiver Details Keys: {list(receiver_details.keys())}")
    logging.info(f"[message_map] Sender Position: {sender_position}")
    logging.info(f"[message_map] All Positions: {positions}")
    logging.info(f"[message_map] All Reception Details: {reception_details}")
    if receiver_ids_list:
        rid = receiver_ids_list[0]
        logging.info(f"[message_map] Sample Position for {rid}: {receiver_positions.get(rid)}")
        logging.info(f"[message_map] Sample Details for {rid}: {receiver_details.get(rid)}")

    return render_template(
        "message_map.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,  # Pass current node info for names
        message=message,
        sender_position=sender_position,
        receiver_positions=receiver_positions,
        receiver_details=receiver_details,
        convex_hull_area_km2=convex_hull_area_km2,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now()  # Page generation time
    )

@app.route('/traceroute_map.html')
def traceroute_map():
    traceroute_id = request.args.get('id')
    if not traceroute_id:
        abort(404)
        
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    
    # Get traceroute attempt by unique id
    cursor = md.db.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM traceroute WHERE traceroute_id = %s
    """, (traceroute_id,))
    traceroute_data = cursor.fetchone()
    if not traceroute_data:
        abort(404)
    
    # Format the forward route data
    route = []
    if traceroute_data['route']:
        route = [int(hop) for hop in traceroute_data['route'].split(';') if hop]
    
    # Format the return route data
    route_back = []
    if traceroute_data['route_back']:
        route_back = [int(hop) for hop in traceroute_data['route_back'].split(';') if hop]
    
    # Format the forward SNR values and scale by dividing by 4
    snr_towards = []
    if traceroute_data['snr_towards']:
        snr_towards = [float(s)/4.0 for s in traceroute_data['snr_towards'].split(';') if s]
    
    # Format the return SNR values and scale by dividing by 4
    snr_back = []
    if traceroute_data['snr_back']:
        snr_back = [float(s)/4.0 for s in traceroute_data['snr_back'].split(';') if s]
    
    # Create a clean traceroute object for the template
    traceroute = {
        'id': traceroute_data['traceroute_id'],
        'from_id': traceroute_data['from_id'],
        'from_id_hex': utils.convert_node_id_from_int_to_hex(traceroute_data['from_id']),
        'to_id': traceroute_data['to_id'],
        'to_id_hex': utils.convert_node_id_from_int_to_hex(traceroute_data['to_id']),
        'ts_created': traceroute_data['ts_created'],
        'route': route,
        'route_back': route_back,
        'snr_towards': snr_towards,
        'snr_back': snr_back,
        'success': traceroute_data['success']
    }
    
    cursor.close()

    # --- Build traceroute_positions dict for historical accuracy ---
    node_ids = set([traceroute['from_id'], traceroute['to_id']] + traceroute['route'] + traceroute['route_back'])
    traceroute_positions = {}
    ts_created = traceroute['ts_created']
    # If ts_created is a datetime, convert to timestamp
    if hasattr(ts_created, 'timestamp'):
        ts_created = ts_created.timestamp()
    for node_id in node_ids:
        pos = md.get_position_at_time(node_id, ts_created)
        if not pos and node_id in nodes and nodes[node_id].position:
            pos_obj = nodes[node_id].position
            # Convert to dict if needed
            if hasattr(pos_obj, '__dict__'):
                pos = dict(pos_obj.__dict__)
            else:
                pos = dict(pos_obj)
            # Ensure position_time is present and properly formatted
            if 'position_time' not in pos or not pos['position_time']:
                if hasattr(pos_obj, 'position_time') and pos_obj.position_time:
                    pt = pos_obj.position_time
                    if isinstance(pt, datetime.datetime):
                        pos['position_time'] = pt.timestamp()
                    else:
                        pos['position_time'] = pt
                else:
                    pos['position_time'] = None
        if pos:
            traceroute_positions[node_id] = pos

    
    return render_template(
        "traceroute_map.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        traceroute=traceroute,
        traceroute_positions=traceroute_positions,  # <-- pass to template
        utils=utils,
        meshtastic_support=meshtastic_support,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now()
    )

@app.route('/graph.html')
@cache.cached(timeout=60, query_string=True) # Cache based on query string (view type)
def graph():
    view_type = request.args.get('view', 'merged') # Default to merged view
    md = get_meshdata()
    if not md:
        abort(503, description="Database connection unavailable")

    # Get graph data using the new method
    graph_data = md.get_graph_data(view_type=view_type)

    # Log graph data size for debugging
    logging.debug(f"Graph data for view '{view_type}': {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges.")

    return render_template(
        "graph.html.j2",
        auth=auth(),
        config=config,
        nodes=md.get_nodes(),  # Pass full nodes list for lookups in template
        graph=graph_data,
        view_type=view_type,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/graph2.html')
@cache.cached(timeout=60, query_string=True)  # Cache based on query string (view type)
def graph2():
    view_type = request.args.get('view', 'merged')  # Default to merged view
    md = get_meshdata()
    if not md:
        abort(503, description="Database connection unavailable")

    # Get graph data using the new method
    graph_data = md.get_graph_data(view_type=view_type)

    # Log graph data size for debugging
    logging.debug(f"Graph data for view '{view_type}': {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges.")

    return render_template(
        "graph2.html.j2",
        auth=auth(),
        config=config,
        nodes=md.get_nodes(),  # Pass full nodes list for lookups in template
        graph=graph_data,
        view_type=view_type,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/graph3.html')
@cache.cached(timeout=60, query_string=True)  # Cache based on query string (view type)
def graph3():
    view_type = request.args.get('view', 'merged')  # Default to merged view
    md = get_meshdata()
    if not md:
        abort(503, description="Database connection unavailable")

    # Get graph data using the new method
    graph_data = md.get_graph_data(view_type=view_type)

    # Log graph data size for debugging
    logging.debug(f"Graph data for view '{view_type}': {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges.")

    return render_template(
        "graph3.html.j2",
        auth=auth(),
        config=config,
        nodes=md.get_nodes(),  # Pass full nodes list for lookups in template
        graph=graph_data,
        view_type=view_type,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/graph4.html')
@cache.cached(timeout=60, query_string=True)  # Cache based on query string (view type)
def graph4():
    view_type = request.args.get('view', 'merged')  # Default to merged view
    md = get_meshdata()
    if not md:
        abort(503, description="Database connection unavailable")

    # Get graph data using the new method
    graph_data = md.get_graph_data(view_type=view_type)

    # Log graph data size for debugging
    logging.debug(f"Graph data for view '{view_type}': {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges.")

    return render_template(
        "graph4.html.j2",
        auth=auth(),
        config=config,
        nodes=md.get_nodes(),  # Pass full nodes list for lookups in template
        graph=graph_data,
        view_type=view_type,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/map.html')
def map():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    
    # Get timeout from config
    zero_hop_timeout = int(config.get("server", "zero_hop_timeout", fallback=43200))
    cutoff_time = int(time.time()) - zero_hop_timeout
    
    # Get zero-hop data for all nodes
    cursor = md.db.cursor(dictionary=True)
    zero_hop_data = {}
    
    # Query for all zero-hop messages
    cursor.execute("""
        SELECT 
            r.from_id,
            r.received_by_id,
            COUNT(*) AS count,
            MAX(r.rx_snr) AS best_snr,
            AVG(r.rx_snr) AS avg_snr,
            MAX(r.rx_time) AS last_rx_time
        FROM 
            message_reception r
        WHERE
            (
                (r.hop_limit IS NULL AND r.hop_start IS NULL)
                OR
                (r.hop_start - r.hop_limit = 0)
            )
            AND r.rx_time > %s
        GROUP BY 
            r.from_id, r.received_by_id
        ORDER BY
            last_rx_time DESC
    """, (cutoff_time,))
    
    for row in cursor.fetchall():
        from_id = utils.convert_node_id_from_int_to_hex(row['from_id'])
        received_by_id = utils.convert_node_id_from_int_to_hex(row['received_by_id'])
        
        if from_id not in zero_hop_data:
            zero_hop_data[from_id] = {'heard': [], 'heard_by': []}
        if received_by_id not in zero_hop_data:
            zero_hop_data[received_by_id] = {'heard': [], 'heard_by': []}
            
        # Add to heard_by list of sender
        zero_hop_data[from_id]['heard_by'].append({
            'node_id': received_by_id,
            'count': row['count'],
            'best_snr': row['best_snr'],
            'avg_snr': row['avg_snr'],
            'last_rx_time': row['last_rx_time']
        })
        
        # Add to heard list of receiver
        zero_hop_data[received_by_id]['heard'].append({
            'node_id': from_id,
            'count': row['count'],
            'best_snr': row['best_snr'],
            'avg_snr': row['avg_snr'],
            'last_rx_time': row['last_rx_time']
        })
    
    cursor.close()
    
    return render_template(
        "map.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        zero_hop_data=zero_hop_data,
        zero_hop_timeout=zero_hop_timeout,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        Channel=meshtastic_support.Channel  # Add Channel enum to template context
    )

@app.route('/neighbors.html')
def neighbors():
    view_type = request.args.get('view', 'neighbor_info') # Default to neighbor_info
    md = get_meshdata()
    if not md:
        abort(503, description="Database connection unavailable")

    # Get base node data (already optimized)
    nodes = md.get_nodes()
    if not nodes:
        # Handle case with no nodes gracefully
        return render_template(
            "neighbors.html.j2",
            auth=auth(), config=config, nodes={},
            active_nodes_with_connections={}, view_type=view_type,
            utils=utils, datetime=datetime.datetime, timestamp=datetime.datetime.now()
        )

    # Get neighbors data using the new method
    active_nodes_data = md.get_neighbors_data(view_type=view_type)

    # Sort final results by last heard time
    active_nodes_data = dict(sorted(
        active_nodes_data.items(),
        key=lambda item: item[1].get('last_heard', datetime.datetime.min),
        reverse=True
    ))

    return render_template(
        "neighbors.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes, # Pass full nodes list for lookups in template
        active_nodes_with_connections=active_nodes_data, # Pass the processed data
        view_type=view_type,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )

@app.route('/telemetry.html')
def telemetry():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    telemetry = md.get_telemetry_all()
    return render_template(
        "telemetry.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        telemetry=telemetry,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now()
    )


@app.route('/traceroutes.html')
def traceroutes():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    page = request.args.get('page', 1, type=int)
    per_page = 100
    
    nodes = md.get_nodes()
    traceroute_data = md.get_traceroutes(page=page, per_page=per_page)
    
    # Calculate pagination info
    total = traceroute_data['total']
    start_item = (page - 1) * per_page + 1 if total > 0 else 0
    end_item = min(page * per_page, total)
    
    # Create pagination info
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': total,
        'items': traceroute_data['items'],
        'pages': (total + per_page - 1) // per_page,
        'has_prev': page > 1,
        'has_next': page * per_page < total,
        'prev_num': page - 1,
        'next_num': page + 1,
        'start_item': start_item,
        'end_item': end_item
    }
    
    return render_template(
        "traceroutes.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        traceroutes=traceroute_data['items'],
        pagination=pagination,
        utils=utils,
        meshtastic_support=meshtastic_support,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        meshdata=md  # Add meshdata to template context
    )


@app.route('/logs.html')
def logs():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    logs = md.get_logs()
    return render_template(
        "logs.html.j2",
        auth=auth(),
        config=config,
        logs=logs,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        json=json
    )


@app.route('/monday.html')
def monday():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    chat = md.get_chat()
    monday = MeshtasticMonday(chat["items"]).get_data()
    return render_template(
        "monday.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        monday=monday,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/mynodes.html')
def mynodes():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    nodes = md.get_nodes()
    owner = auth()
    if not owner:
        return redirect(url_for('login'))
    mynodes = utils.get_owner_nodes(nodes, owner["email"])
    return render_template(
        "mynodes.html.j2",
        auth=owner,
        config=config,
        nodes=mynodes,
        show_inactive=True,
        hardware=meshtastic_support.HardwareModel,
        meshtastic_support=meshtastic_support,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/linknode.html')
def link_node():
    owner = auth()
    if not owner:
        return redirect(url_for('login'))
    reg = Register()
    otp = reg.get_otp(
        owner["email"]
    )
    return render_template(
        "link_node.html.j2",
        auth=owner,
        otp=otp,
        config=config
    )


@app.route('/register.html', methods=['GET', 'POST'])
def register():
    error_message = None
    if request.method == 'POST':
        email = request.form.get('email')
        username = request.form.get('username')
        password = request.form.get('password')
        reg = Register()
        res = reg.register(username, email, password)
        if "error" in res:
            error_message = res["error"]
        elif "success" in res:
            return serve_index(success_message=res["success"])

    return render_template(
        "register.html.j2",
        auth=auth(),
        config=config,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        error_message=error_message
    )


@app.route('/login.html', methods=['GET', 'POST'])
def login(success_message=None, error_message=None):
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        reg = Register()
        res = reg.authenticate(email, password)
        if "error" in res:
            error_message = res["error"]
        elif "success" in res:
            jwt = res["success"]
            resp = make_response(redirect(url_for('mynodes')))
            resp.set_cookie("jwt", jwt)
            return resp
    return render_template(
            "login.html.j2",
            auth=auth(),
            config=config,
            datetime=datetime.datetime,
            timestamp=datetime.datetime.now(),
            success_message=success_message,
            error_message=error_message
        )


@app.route('/logout.html')
def logout():
    resp = make_response(redirect(url_for('serve_index')))
    resp.set_cookie('jwt', '', expires=0)
    return resp


@app.route('/verify')
def verify():
    code = request.args.get('c')
    reg = Register()
    res = reg.verify_account(code)
    if "error" in res:
        return serve_index(error_message=res["error"])
    elif "success" in res:
        return login(success_message=res["success"])
    return serve_index()


@app.route('/<path:filename>')
def serve_static(filename):
    nodep = r"node\_(\w{8})\.html"
    userp = r"user\_(\w+)\.html"

    if re.match(nodep, filename):
        md = get_meshdata()
        if not md: # Check if MeshData failed to initialize
            abort(503, description="Database connection unavailable")
        match = re.match(nodep, filename)
        node = match.group(1)
        nodes = md.get_nodes()
        if node not in nodes:
            abort(404)
        node_id = utils.convert_node_id_from_hex_to_int(node)
        node_telemetry = md.get_node_telemetry(node_id)
        node_route = md.get_route_coordinates(node_id)
        telemetry_graph = draw_graph(node_telemetry)
        lp = LOSProfile(nodes, node_id, config, cache)

        # Get the max_distance from config, default to 5000 meters (5 km)
        max_distance_km = int(config.get("los", "max_distance", fallback=5000)) / 1000  # Convert to kilometers

        # Check if LOS Profile rendering is enabled
        los_enabled = config.getboolean("los", "enabled", fallback=False)

        # Get timeout from config
        zero_hop_timeout = int(config.get("server", "zero_hop_timeout", fallback=43200))  # Default 12 hours
        cutoff_time = int(time.time()) - zero_hop_timeout
        
        # Query for zero-hop messages heard by this node (within timeout period)
        db = md.db
        zero_hop_heard = []
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                r.from_id,
                COUNT(*) AS count,
                MAX(r.rx_snr) AS best_snr,
                AVG(r.rx_snr) AS avg_snr,
                MAX(r.rx_time) AS last_rx_time
            FROM 
                message_reception r
            WHERE
                r.received_by_id = %s
                AND (
                    (r.hop_limit IS NULL AND r.hop_start IS NULL)
                    OR
                    (r.hop_start - r.hop_limit = 0)
                )
                AND r.rx_time > %s
            GROUP BY 
                r.from_id
            ORDER BY
                last_rx_time DESC
        """, (node_id, cutoff_time))
        zero_hop_heard = cursor.fetchall()
        
        # Query for zero-hop messages sent by this node and heard by others (within timeout period)
        zero_hop_heard_by = []
        cursor.execute("""
            SELECT 
                r.received_by_id,
                COUNT(*) AS count,
                MAX(r.rx_snr) AS best_snr,
                AVG(r.rx_snr) AS avg_snr,
                MAX(r.rx_time) AS last_rx_time
            FROM 
                message_reception r
            WHERE
                r.from_id = %s
                AND (
                    (r.hop_limit IS NULL AND r.hop_start IS NULL)
                    OR
                    (r.hop_start - r.hop_limit = 0)
                )
                AND r.rx_time > %s
            GROUP BY 
                r.received_by_id
            ORDER BY
                last_rx_time DESC
        """, (node_id, cutoff_time))
        zero_hop_heard_by = cursor.fetchall()
        cursor.close()
        
        return render_template(
            f"node.html.j2",
            auth=auth(),
            config=config,
            node=nodes[node],
            nodes=nodes,
            hardware=meshtastic_support.HardwareModel,
            meshtastic_support=meshtastic_support,
            los_profiles=lp.get_profiles() if los_enabled else {},  # Render only if enabled
            telemetry_graph=telemetry_graph,
            node_route=node_route,
            utils=utils,
            datetime=datetime.datetime,
            timestamp=datetime.datetime.now(),
            zero_hop_heard=zero_hop_heard,
            zero_hop_heard_by=zero_hop_heard_by,
            zero_hop_timeout=zero_hop_timeout,
            max_distance=max_distance_km
        )

    if re.match(userp, filename):
        match = re.match(userp, filename)
        username = match.group(1)
        md = get_meshdata()
        if not md: # Check if MeshData failed to initialize
            abort(503, description="Database connection unavailable")
        owner = md.get_user(username)
        if not owner:
            abort(404)
        nodes = md.get_nodes()
        owner_nodes = utils.get_owner_nodes(nodes, owner["email"])
        return render_template(
            "user.html.j2",
            auth=auth(),
            username=username,
            config=config,
            nodes=owner_nodes,
            show_inactive=True,
            hardware=meshtastic_support.HardwareModel,
            meshtastic_support=meshtastic_support,
            utils=utils,
            datetime=datetime.datetime,
            timestamp=datetime.datetime.now(),
        )

    return send_from_directory("www", filename)


@app.route('/metrics.html')
@cache.cached(timeout=60)  # Cache for 60 seconds
def metrics():
    return render_template(
        "metrics.html.j2",
        auth=auth(),
        config=config,
        Channel=meshtastic_support.Channel,
        utils=utils
    )

@app.route('/api/metrics')
def get_metrics():
    md = get_meshdata()
    if not md: # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    
    try:
        # Get time range from request parameters
        time_range = request.args.get('time_range', 'day')  # day, week, month, year, all
        channel = request.args.get('channel', 'all')  # Get channel parameter
        
        # Set time range based on parameter
        end_time = datetime.datetime.now()
        if time_range == 'week':
            start_time = end_time - datetime.timedelta(days=7)
            bucket_size = 180  # 3 hours in minutes
        elif time_range == 'month':
            start_time = end_time - datetime.timedelta(days=30)
            bucket_size = 720  # 12 hours in minutes
        elif time_range == 'year':
            start_time = end_time - datetime.timedelta(days=365)
            bucket_size = 2880  # 2 days in minutes
        elif time_range == 'all':
            # For 'all', we'll first check the data range in the database
            cursor = md.db.cursor(dictionary=True)
            cursor.execute("SELECT MIN(ts_created) as min_time FROM telemetry")
            min_time = cursor.fetchone()['min_time']
            cursor.close()
            
            if min_time:
                start_time = min_time
            else:
                # Default to 1 year if no data
                start_time = end_time - datetime.timedelta(days=365)
            
            bucket_size = 10080  # 7 days in minutes
        else:  # Default to 'day'
            start_time = end_time - datetime.timedelta(hours=24)
            bucket_size = 30  # 30 minutes
        
        # Convert timestamps to the correct format for MySQL
        start_timestamp = start_time.strftime('%Y-%m-%d %H:%M:%S')
        end_timestamp = end_time.strftime('%Y-%m-%d %H:%M:%S')
        
        # Format string for time buckets based on bucket size
        if bucket_size >= 10080:  # 7 days or more
            time_format = '%Y-%m-%d'  # Daily format
        elif bucket_size >= 1440:  # 1 day or more
            time_format = '%Y-%m-%d %H:00'  # Hourly format
        else:
            time_format = '%Y-%m-%d %H:%i'  # Minute format
        
        cursor = md.db.cursor(dictionary=True)
        
        # First, generate a series of time slots
        time_slots_query = f"""
            WITH RECURSIVE time_slots AS (
                SELECT DATE_FORMAT(
                    DATE_ADD(%s, INTERVAL -MOD(MINUTE(%s), {bucket_size}) MINUTE),
                    '{time_format}'
                ) as time_slot
                UNION ALL
                SELECT DATE_FORMAT(
                    DATE_ADD(
                        STR_TO_DATE(time_slot, '{time_format}'),
                        INTERVAL {bucket_size} MINUTE
                    ),
                    '{time_format}'
                )
                FROM time_slots
                WHERE DATE_ADD(
                    STR_TO_DATE(time_slot, '{time_format}'),
                    INTERVAL {bucket_size} MINUTE
                ) <= %s
            )
            SELECT time_slot FROM time_slots
        """
        cursor.execute(time_slots_query, (start_timestamp, start_timestamp, end_timestamp))
        time_slots = [row['time_slot'] for row in cursor.fetchall()]
        
        # Add channel condition if specified
        channel_condition = ""
        if channel != 'all':
            # Only apply channel condition to tables that have a channel column
            channel_condition_text = f" AND channel = {channel}"
            channel_condition_telemetry = f" AND channel = {channel}"
            channel_condition_reception = f" AND EXISTS (SELECT 1 FROM text t WHERE t.message_id = message_reception.message_id AND t.channel = {channel})"
        else:
            channel_condition_text = ""
            channel_condition_telemetry = ""
            channel_condition_reception = ""
        
        # Nodes Online Query
        nodes_online_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                COUNT(DISTINCT id) as node_count
            FROM telemetry
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_telemetry}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(nodes_online_query, (start_timestamp, end_timestamp))
        nodes_online_data = {row['time_slot']: row['node_count'] for row in cursor.fetchall()}
        
        # Message Traffic Query
        message_traffic_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                COUNT(*) as message_count
            FROM text
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_text}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(message_traffic_query, (start_timestamp, end_timestamp))
        message_traffic_data = {row['time_slot']: row['message_count'] for row in cursor.fetchall()}
        
        # Channel Utilization Query
        channel_util_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                AVG(channel_utilization) as avg_util
            FROM telemetry
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_telemetry}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(channel_util_query, (start_timestamp, end_timestamp))
        channel_util_data = {row['time_slot']: float(row['avg_util']) if row['avg_util'] is not None else 0.0 for row in cursor.fetchall()}
        
        # Battery Levels Query
        battery_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                AVG(battery_level) as avg_battery
            FROM telemetry
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_telemetry}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(battery_query, (start_timestamp, end_timestamp))
        battery_data = {row['time_slot']: float(row['avg_battery']) if row['avg_battery'] is not None else 0.0 for row in cursor.fetchall()}
        
        # Temperature Query
        temperature_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                AVG(temperature) as avg_temp
            FROM telemetry
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_telemetry}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(temperature_query, (start_timestamp, end_timestamp))
        temperature_data = {row['time_slot']: float(row['avg_temp']) if row['avg_temp'] is not None else 0.0 for row in cursor.fetchall()}
        
        # SNR Query
        snr_query = f"""
            SELECT 
                DATE_FORMAT(
                    DATE_ADD(
                        ts_created,
                        INTERVAL -MOD(MINUTE(ts_created), {bucket_size}) MINUTE
                    ),
                    '{time_format}'
                ) as time_slot,
                AVG(rx_snr) as avg_snr
            FROM message_reception
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_reception}
            GROUP BY time_slot
            ORDER BY time_slot
        """
        cursor.execute(snr_query, (start_timestamp, end_timestamp))
        snr_data = {row['time_slot']: float(row['avg_snr']) if row['avg_snr'] is not None else 0.0 for row in cursor.fetchall()}
        
        cursor.close()
        
        # --- Moving Average Helper ---
        def moving_average_centered(data_list, window_minutes, bucket_size_minutes):
            # data_list: list of floats (same order as time_slots)
            # window_minutes: total window size (e.g., 120 for 2 hours)
            # bucket_size_minutes: size of each bucket (e.g., 30 for 30 min)
            n = len(data_list)
            result = []
            half_window = window_minutes // 2
            buckets_per_window = max(1, window_minutes // bucket_size_minutes)
            for i in range(n):
                # Centered window: find all indices within window centered at i
                center_time = i
                window_indices = []
                for j in range(n):
                    if abs(j - center_time) * bucket_size_minutes <= half_window:
                        window_indices.append(j)
                if window_indices:
                    avg = sum(data_list[j] for j in window_indices) / len(window_indices)
                else:
                    avg = data_list[i]
                result.append(avg)
            return result

        # --- Get metrics_average_interval from config ---
        metrics_avg_interval = int(config.get('server', 'metrics_average_interval', fallback=7200))  # seconds
        metrics_avg_minutes = metrics_avg_interval // 60

        # --- Calculate moving averages for relevant metrics ---
        # Determine bucket_size_minutes from bucket_size
        bucket_size_minutes = bucket_size

        # Prepare raw data lists
        nodes_online_raw = [nodes_online_data.get(slot, 0) for slot in time_slots]
        battery_levels_raw = [battery_data.get(slot, 0) for slot in time_slots]
        temperature_raw = [temperature_data.get(slot, 0) for slot in time_slots]
        snr_raw = [snr_data.get(slot, 0) for slot in time_slots]

        # --- Get node_activity_prune_threshold from config ---
        node_activity_prune_threshold = int(config.get('server', 'node_activity_prune_threshold', fallback=7200))  # seconds

        # --- For each time slot, count unique nodes heard in the preceding activity window ---
        # Fetch all telemetry records in the full time range
        cursor = md.db.cursor(dictionary=True)
        cursor.execute(f"""
            SELECT id, ts_created
            FROM telemetry
            WHERE ts_created >= %s AND ts_created <= %s {channel_condition_telemetry}
            ORDER BY ts_created
        """, (start_timestamp, end_timestamp))
        all_telemetry = list(cursor.fetchall())
        # Convert ts_created to datetime for easier comparison
        for row in all_telemetry:
            if isinstance(row['ts_created'], str):
                row['ts_created'] = datetime.datetime.strptime(row['ts_created'], '%Y-%m-%d %H:%M:%S')
        # Precompute for each time slot
        nodes_heard_per_slot = []
        for slot in time_slots:
            # slot is a string, convert to datetime
            if '%H:%M' in time_format or '%H:%i' in time_format:
                slot_time = datetime.datetime.strptime(slot, '%Y-%m-%d %H:%M')
            elif '%H:00' in time_format:
                slot_time = datetime.datetime.strptime(slot, '%Y-%m-%d %H:%M')
            else:
                slot_time = datetime.datetime.strptime(slot, '%Y-%m-%d')
            window_start = slot_time - datetime.timedelta(seconds=node_activity_prune_threshold)
            # Find all node ids with telemetry in [window_start, slot_time]
            active_nodes = set()
            for row in all_telemetry:
                if window_start < row['ts_created'] <= slot_time:
                    active_nodes.add(row['id'])
            nodes_heard_per_slot.append(len(active_nodes))
        # Now apply moving average and round to nearest integer
        nodes_online_smoothed = [round(x) for x in moving_average_centered(nodes_heard_per_slot, metrics_avg_minutes, bucket_size_minutes)]

        # Fill in missing time slots with zeros (for non-averaged metrics)
        result = {
            'nodes_online': {
                'labels': time_slots,
                'data': nodes_online_smoothed
            },
            'message_traffic': {
                'labels': time_slots,
                'data': [message_traffic_data.get(slot, 0) for slot in time_slots]
            },
            'channel_util': {
                'labels': time_slots,
                'data': [channel_util_data.get(slot, 0) for slot in time_slots]
            },
            'battery_levels': {
                'labels': time_slots,
                'data': moving_average_centered(battery_levels_raw, metrics_avg_minutes, bucket_size_minutes)
            },
            'temperature': {
                'labels': time_slots,
                'data': moving_average_centered(temperature_raw, metrics_avg_minutes, bucket_size_minutes)
            },
            'snr': {
                'labels': time_slots,
                'data': moving_average_centered(snr_raw, metrics_avg_minutes, bucket_size_minutes)
            }
        }
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error fetching metrics data: {str(e)}", exc_info=True)
        return jsonify({
            'error': f'Error fetching metrics data: {str(e)}'
        }), 500

@app.route('/api/chattiest-nodes')
def get_chattiest_nodes():
    md = get_meshdata()
    if not md:  # Check if MeshData failed to initialize
        abort(503, description="Database connection unavailable")
    
    # Get filter parameters from request
    time_frame = request.args.get('time_frame', 'day')  # day, week, month, year, all
    message_type = request.args.get('message_type', 'all')  # all, text, position, telemetry
    channel = request.args.get('channel', 'all')  # all or specific channel number
    
    try:
        cursor = md.db.cursor(dictionary=True)
        
        # Build the time frame condition
        time_condition = ""
        if time_frame == 'year':
            time_condition = "WHERE ts_created >= DATE_SUB(NOW(), INTERVAL 1 YEAR)"
        elif time_frame == 'month':
            time_condition = "WHERE ts_created >= DATE_SUB(NOW(), INTERVAL 1 MONTH)"
        elif time_frame == 'week':
            time_condition = "WHERE ts_created >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
        elif time_frame == 'day':
            time_condition = "WHERE ts_created >= DATE_SUB(NOW(), INTERVAL 24 HOUR)"
        
        # Add channel filter if specified - only for text and telemetry tables which have channel column
        channel_condition_text = ""
        channel_condition_telemetry = ""
        if channel != 'all':
            channel_condition_text = f" AND channel = {channel}"
            channel_condition_telemetry = f" AND channel = {channel}"
            if not time_condition:
                channel_condition_text = f"WHERE channel = {channel}"
                channel_condition_telemetry = f"WHERE channel = {channel}"
        
        # Build the message type query based on the selected type
        if message_type == 'all':
            # For text messages, we need to qualify the columns with table aliases
            time_condition_with_prefix = time_condition.replace("WHERE", "WHERE t.").replace(" AND", " AND t.")
            channel_condition_text_with_prefix = channel_condition_text.replace("WHERE", "WHERE t.").replace(" AND", " AND t.")
            
            message_query = (
                "SELECT t.from_id as node_id, t.ts_created, t.channel as channel "
                "FROM text t "
                + time_condition_with_prefix 
                + channel_condition_text_with_prefix + " "
                "UNION ALL "
                "SELECT id as node_id, ts_created, NULL as channel "
                "FROM positionlog "
                + time_condition + " "
                "UNION ALL "
                "SELECT id as node_id, ts_created, channel "
                "FROM telemetry "
                + time_condition 
                + channel_condition_telemetry
            )
        elif message_type == 'text':
            message_query = (
                "SELECT from_id as node_id, ts_created, channel "
                "FROM text "
                + time_condition
                + channel_condition_text
            )
        elif message_type == 'position':
            message_query = (
                "SELECT id as node_id, ts_created, NULL as channel "
                "FROM positionlog "
                + time_condition
            )
        elif message_type == 'telemetry':
            message_query = (
                "SELECT id as node_id, ts_created, channel "
                "FROM telemetry "
                + time_condition
                + channel_condition_telemetry
            )
        else:
            return jsonify({
                'error': f'Invalid message type: {message_type}'
            }), 400
        
        # Query to get the top 20 nodes by message count, including node names and role
        query = """
            WITH messages AS ({message_query})
            SELECT 
                m.node_id as from_id,
                n.long_name,
                n.short_name,
                n.role,
                COUNT(*) as message_count,
                COUNT(DISTINCT DATE_FORMAT(m.ts_created, '%Y-%m-%d')) as active_days,
                MIN(m.ts_created) as first_message,
                MAX(m.ts_created) as last_message,
                CASE 
                    WHEN '{channel}' != 'all' THEN '{channel}'
                    ELSE GROUP_CONCAT(DISTINCT NULLIF(CAST(m.channel AS CHAR), 'NULL'))
                END as channels,
                CASE 
                    WHEN '{channel}' != 'all' THEN 1
                    ELSE COUNT(DISTINCT NULLIF(m.channel, 'NULL'))
                END as channel_count
            FROM 
                messages m
            LEFT JOIN
                nodeinfo n ON m.node_id = n.id
            GROUP BY 
                m.node_id, n.long_name, n.short_name, n.role
            ORDER BY 
                message_count DESC
            LIMIT 20
        """.format(message_query=message_query, channel=channel)
        
        cursor.execute(query)
        results = cursor.fetchall()
        
        # Process the results to format them for the frontend
        chattiest_nodes = []
        for row in results:
            # Convert node ID to hex format
            node_id_hex = utils.convert_node_id_from_int_to_hex(row['from_id'])
            
            # Parse channels string into a list of channel objects
            channels_str = row['channels']
            channels = []
            if channels_str:
                # If we're filtering by channel, just use that channel
                if channel != 'all':
                    channels.append({
                        'id': int(channel),
                        'name': utils.get_channel_name(int(channel))
                    })
                else:
                    # Otherwise process the concatenated list of channels
                    channel_ids = [ch_id for ch_id in channels_str.split(',') if ch_id and ch_id != 'NULL']
                    for ch_id in channel_ids:
                        try:
                            ch_id_int = int(ch_id)
                            channels.append({
                                'id': ch_id_int,
                                'name': utils.get_channel_name(ch_id_int)
                            })
                        except (ValueError, TypeError):
                            continue
            
            # Create node object
            node = {
                'node_id': row['from_id'],
                'node_id_hex': node_id_hex,
                'long_name': row['long_name'] or f"Node {row['from_id']}",  # Fallback if no long name
                'short_name': row['short_name'] or f"Node {row['from_id']}",  # Fallback if no short name
                'role': utils.get_role_name(row['role']),  # Convert role number to name
                'message_count': row['message_count'],
                'active_days': row['active_days'],
                'first_message': row['first_message'].isoformat() if row['first_message'] else None,
                'last_message': row['last_message'].isoformat() if row['last_message'] else None,
                'channels': channels,
                'channel_count': row['channel_count']
            }
            chattiest_nodes.append(node)
        
        return jsonify({
            'chattiest_nodes': chattiest_nodes
        })
        
    except Exception as e:
        logging.error(f"Error fetching chattiest nodes: {str(e)}")
        return jsonify({
            'error': f'Error fetching chattiest nodes: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()

@app.route('/api/telemetry/<int:node_id>')
def api_telemetry(node_id):
    md = get_meshdata()
    telemetry = md.get_telemetry_for_node(node_id)
    return jsonify(telemetry)

def run():
    # Enable Waitress logging
    config = configparser.ConfigParser()
    config.read('config.ini')
    port = int(config["webserver"]["port"])

    waitress_logger = logging.getLogger("waitress")
    waitress_logger.setLevel(logging.DEBUG)  # Enable all logs from Waitress
    #  serve(app, host="0.0.0.0", port=port)
    serve(
        TransLogger(
            app,
            setup_console_handler=False,
            logger=waitress_logger
        ),
        port=port
    )


if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('config.ini')
    port = int(config["webserver"]["port"])
    app.run(debug=True, port=port)
