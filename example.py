import os
from datetime import datetime

from lnhistoryclient.Lnhistoryrequster import LnhistoryRequester, LnhistoryRequesterError

# Example usage of the client
api_key = "YOUR-API-KEY"

with LnhistoryRequester(api_key) as client:
    try:
        # Get a snapshot at a specific time and create NetworkX graph
        timestamp = datetime(2021, 2, 6, 1, 0, 0)

        # Option 1: Get NetworkX graph directly
        graph = client.get_snapshot_at_timestamp(timestamp, return_graph=True, stopwatch=True)
        print(f"Graph created with {graph.number_of_nodes()} nodes and {graph.number_of_edges()} edges")

        print("=" * 50)

        # Option 2: Get graph and save to file
        graph = client.get_snapshot_at_timestamp(
            timestamp, return_graph=True, save_to_file="lightning_network.graphml", format="graphml", stopwatch=True
        )
        print("Graph saved to lightning_network.graphml")

        print("=" * 50)

        # Option 3: Get raw file path for manual processing
        file_path = client.get_snapshot_at_timestamp(timestamp, return_graph=False, stopwatch=True)
        print(f"Raw data available at: {file_path}")
        # Don't forget to clean up the temp file when done
        os.unlink(file_path)

    except LnhistoryRequesterError as e:
        print(f"API Error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
