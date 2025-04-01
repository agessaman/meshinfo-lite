import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from meshtastic import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2
from google.protobuf.json_format import MessageToJson
from meshdata import MeshData
import configparser
import json
import logging

config = configparser.ConfigParser()
config.read("config.ini")

DEFAULT_KEY = config["mesh"]["channel_key"]


def decrypt_packet(mp):
    key_bytes = base64.b64decode(DEFAULT_KEY)
    nonce_packet_id = getattr(mp, "id").to_bytes(8, "little")
    nonce_from_node = getattr(mp, "from").to_bytes(8, "little")
    nonce = nonce_packet_id + nonce_from_node
    cipher = Cipher(
        algorithms.AES(key_bytes),
        modes.CTR(nonce),
        backend=default_backend()
    )
    decryptor = cipher.decryptor()
    decrypted_bytes = decryptor.update(
        getattr(mp, "encrypted")
    ) + decryptor.finalize()
    data = mesh_pb2.Data()
    data.ParseFromString(decrypted_bytes)
    mp.decoded.CopyFrom(data)
    return mp


def get_packet(payload):
    mp = None
    try:
        se = mqtt_pb2.ServiceEnvelope()
        se.ParseFromString(payload)
        mp = se.packet
        
        # Extract hop information if available
        try:
            if hasattr(mp, 'hop_limit'):
                mp.hop_limit = getattr(mp, 'hop_limit', 0)
            else:
                mp.hop_limit = None
                
            if hasattr(mp, 'hop_start'):
                mp.hop_start = getattr(mp, 'hop_start', 0)
            else:
                mp.hop_start = None
                
        except Exception as e:
            if config.get("server", "debug") == "true":
                logging.debug(f"Hop information extraction failed: {e}")
            mp.hop_limit = None
            mp.hop_start = None
            
        if mp.HasField("encrypted") and not mp.HasField("decoded"):
            return decrypt_packet(mp)
    except Exception as e:
        logging.error("Failed to decode payload " + str(e))
    return mp


def to_json(msg):
    return json.loads(
        MessageToJson(
            msg,
            preserving_proto_field_name=True,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            use_integers_for_enums=True
        )
    )


def get_data(msg):
    j = to_json(msg)
    if "decoded" not in j:
        return None
    
    # Add hop information to the JSON data
    if hasattr(msg, 'hop_limit'):
        j["hop_limit"] = msg.hop_limit
    if hasattr(msg, 'hop_start'):
        j["hop_start"] = msg.hop_start
    
    portnum = j["decoded"]["portnum"]
    if portnum == portnums_pb2.NODEINFO_APP:
        j["type"] = "nodeinfo"
        j["decoded"]["json_payload"] = to_json(
            mesh_pb2.User().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.MAP_REPORT_APP:
        j["type"] = "mapreport"
        j["decoded"]["json_payload"] = to_json(
            mqtt_pb2.MapReport().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.TEXT_MESSAGE_APP:
        j["type"] = "text"
        j["decoded"]["json_payload"] = {
            "text": msg.decoded.payload
        }
    elif portnum == portnums_pb2.NEIGHBORINFO_APP:
        j["type"] = "neighborinfo"
        j["decoded"]["json_payload"] = to_json(
            mesh_pb2.NeighborInfo().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.ROUTING_APP:
        j["type"] = "routing"
        j["decoded"]["json_payload"] = to_json(
            mesh_pb2.Routing().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.TRACEROUTE_APP:
        j["type"] = "traceroute"
        j["decoded"]["json_payload"] = to_json(
            mesh_pb2.RouteDiscovery().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.POSITION_APP:
        j["type"] = "position"
        j["decoded"]["json_payload"] = to_json(
            mesh_pb2.Position().FromString(msg.decoded.payload)
        )
    elif portnum == portnums_pb2.TELEMETRY_APP:
        j["type"] = "telemetry"
        j["decoded"]["json_payload"] = to_json(
            telemetry_pb2.Telemetry().FromString(msg.decoded.payload)
        )
    if "type" in j:
        msg_type = j["type"]
        msg_from = j["from"]
        
        # Log hop information if it's a text message with hop data
        if msg_type == "text" and j.get("hop_limit") is not None and j.get("hop_start") is not None:
            hop_count = j["hop_start"] - j["hop_limit"]
            logging.info(f"Received {msg_type} from {msg_from} with {hop_count} hops ({j['hop_limit']}/{j['hop_start']})")
        else:
            logging.info(f"Received {msg_type} from {msg_from}")
            
    return j


def process_payload(payload, topic):
    md = MeshData()
    mp = get_packet(payload)
    if mp:
        data = get_data(mp)
        md.store(data, topic)
