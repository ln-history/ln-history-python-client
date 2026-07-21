from datetime import datetime

from sshtunnel import SSHTunnelForwarder

from lnhistoryclient.Lnhistoryrequester import LnhistoryRequester, LnhistoryRequesterError

api_key = "mQd2frqAaMts5wwzEgHrdhF7y"

# 1. Establish the SSH Tunnel
# Replace the IP, username, and key path with your actual server details
server_ip = "185.234.72.91"  # Or your public IP
ssh_username = "bitcoin"

try:
    with SSHTunnelForwarder(
        (server_ip, 22),
        ssh_username="bitcoin",
        ssh_password="Spd3XWjk7sfgxzjnURKx2DcF2",
        remote_bind_address=("127.0.0.1", 5000),  # The port Docker exposed on the server
        local_bind_address=("127.0.0.1", 5000),  # The port to use on your local machine
    ) as tunnel:

        print("SSH Tunnel established successfully!")

        # 2. Connect the API client through the tunnel[cite: 1]
        with LnhistoryRequester(api_key=api_key) as client:

            # Call the method on 'client', NOT 'LnhistoryRequester'
            graph = client.get_snapshot_at_timestamp(timestamp=datetime(2026, 1, 20))

            print(f"Success! Graph has {graph.number_of_nodes()} nodes.")

except LnhistoryRequesterError as e:
    print(f"API Error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
