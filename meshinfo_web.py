from flask import (
    Flask,
    send_from_directory,
    render_template,
    request,
    make_response,
    redirect,
    url_for,
    abort
)
from waitress import serve
from paste.translogger import TransLogger
import configparser
import logging
import os

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
import time
import re

app = Flask(__name__)

# Make timezone utilities available to templates
app.jinja_env.globals.update(convert_to_local=convert_to_local)
app.jinja_env.globals.update(format_timestamp=format_timestamp)
app.jinja_env.globals.update(time_ago=time_ago)

config = configparser.ConfigParser()
config.read("config.ini")


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
def serve_index(success_message=None, error_message=None):
    md = MeshData()
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
def nodes():
    md = MeshData()
    nodes = md.get_nodes()
    latest = md.get_latest_node()
    return render_template(
        "nodes.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        latest=latest,
        hardware=meshtastic_support.HardwareModel,
        meshtastic_support=meshtastic_support,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/allnodes.html')
def allnodes():
    md = MeshData()
    nodes = md.get_nodes()
    latest = md.get_latest_node()
    return render_template(
        "allnodes.html.j2",
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


@app.route('/chat.html')
def chat():
    md = MeshData()
    nodes = md.get_nodes()
    chat = md.get_chat()
    return render_template(
        "chat.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        chat=chat,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
        debug=False,
    )


@app.route('/graph.html')
def graph():
    md = MeshData()
    nodes = md.get_nodes()
    graph = md.graph_nodes()
    return render_template(
        "graph.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        graph=graph,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/map.html')
def map():
    md = MeshData()
    nodes = md.get_nodes()
    return render_template(
        "map.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        utils=utils,
        datetime=datetime,
        timestamp=datetime.datetime.now()
    )


@app.route('/neighbors.html')
def neighbors():
    md = MeshData()
    nodes = md.get_nodes()
    active_nodes_with_neighbors = {
        node_id: dict(node)
        for node_id, node in nodes.items()
        if node.get("active") and node.get("neighbors")
    }
    return render_template(
        "neighbors.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        active_nodes_with_neighbors=active_nodes_with_neighbors,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/telemetry.html')
def telemetry():
    md = MeshData()
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
    md = MeshData()
    nodes = md.get_nodes()
    traceroutes = md.get_traceroutes()
    return render_template(
        "traceroutes.html.j2",
        auth=auth(),
        config=config,
        nodes=nodes,
        traceroutes=traceroutes,
        utils=utils,
        datetime=datetime.datetime,
        timestamp=datetime.datetime.now(),
    )


@app.route('/logs.html')
def logs():
    md = MeshData()
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
    md = MeshData()
    nodes = md.get_nodes()
    chat = md.get_chat()
    monday = MeshtasticMonday(chat).get_data()
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
    md = MeshData()
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
        md = MeshData()
        match = re.match(nodep, filename)
        node = match.group(1)
        nodes = md.get_nodes()
        if node not in nodes:
            abort(404)
        node_id = utils.convert_node_id_from_hex_to_int(node)
        node_telemetry = md.get_node_telemetry(node_id)
        node_route = md.get_route_coordinates(node_id)
        telemetry_graph = draw_graph(node_telemetry)
        lp = LOSProfile(nodes, node_id)
        return render_template(
                f"node.html.j2",
                auth=auth(),
                config=config,
                node=nodes[node],
                nodes=nodes,
                hardware=meshtastic_support.HardwareModel,
                meshtastic_support=meshtastic_support,
                los_profiles=lp.get_profiles(),
                telemetry_graph=telemetry_graph,
                node_route=node_route,
                utils=utils,
                datetime=datetime.datetime,
                timestamp=datetime.datetime.now(),
            )
    if re.match(userp, filename):
        match = re.match(userp, filename)
        username = match.group(1)
        md = MeshData()
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
