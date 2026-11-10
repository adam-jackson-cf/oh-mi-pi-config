import socket


def test_price_service_reachable():
    with socket.create_connection(("127.0.0.1", 9), timeout=1):
        pass
