from flask import Flask, jsonify, request
import time

app = Flask(__name__)

network_state = {
    "state": "normal"
}


@app.route("/network/status", methods=["GET"])
def get_status():
    return jsonify(network_state)


@app.route("/network/state", methods=["POST"])
def set_state():
    data = request.get_json()

    if not data or "state" not in data:
        return jsonify({
            "error": "Missing state"
        }), 400

    new_state = data["state"]

    valid_states = [
        "normal",
        "throttled",
        "disconnected"
    ]

    if new_state not in valid_states:
        return jsonify({
            "error": "Invalid state",
            "valid_states": valid_states
        }), 400

    network_state["state"] = new_state

    return jsonify({
        "message": "Network state updated",
        "state": new_state
    })


@app.route("/cloud/test", methods=["GET"])
def cloud_test():
    state = network_state["state"]

    if state == "normal":
        return jsonify({
            "status": "success",
            "message": "Cloud connection normal"
        })

    elif state == "throttled":
        time.sleep(5)

        return jsonify({
            "status": "delayed",
            "message": "Cloud connection throttled"
        })

    elif state == "disconnected":
        return jsonify({
            "status": "failed",
            "message": "Cloud connection unavailable"
        }), 503


if __name__ == "__main__":
    print("RescueGrid Network Simulator Started")

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )