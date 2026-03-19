import json
from datetime import datetime

class GossipProtocol:
    @staticmethod
    def format_announcement(node_id, zone, sensors):
        """Format the initial 'Hello' discovery message."""
        return json.dumps({
            "type": "DISCOVERY",
            "node_id": node_id,
            "zone": zone,
            "sensors": sensors,
            "timestamp": datetime.utcnow().isoformat()
        })

    @staticmethod
    def format_telemetry(node_id, torque, position, state="OK"):
        """Format the 'Gossip' message about device health."""
        return json.dumps({
            "type": "GOSSIP",
            "node_id": node_id,
            "data": {
                "torque": torque,
                "position": position,
                "status": state
            },
            "timestamp": datetime.utcnow().isoformat()
        })